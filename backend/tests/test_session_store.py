"""阶段 2 / P5 测试：SQLite 会话存储（消息、历史过滤、检索 trace、旧库迁移）。"""
import sqlite3

from app.services.session_store import SessionStore


def test_messages_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.add_message("s1", "user", "你好")
    store.add_message("s1", "assistant", "你好，有什么可以帮你？", sources=[{"index": 1}])
    store.add_message("s2", "user", "别的会话")

    history = store.get_history("s1")
    assert [h["role"] for h in history] == ["user", "assistant"]
    assert history[0]["content"] == "你好"
    assert history[1]["sources"] == [{"index": 1}]

    limited = store.get_history("s1", limit=1)
    assert len(limited) == 1 and limited[0]["role"] == "assistant"


def test_merged_sources_roundtrip(tmp_path):
    """V1.2：合并卡片的嵌套结构（indices/headings/excerpts）必须可无损往返。"""
    store = SessionStore(tmp_path / "s.db")
    merged = [
        {
            "index": 1,
            "indices": [1, 3, 5],
            "chunk_count": 3,
            "title": "3. Python 演示 — Python 3.15.0 文档",
            "file_path": r"D:\docs\python-3.15.0-tutorial\introduction.html",
            "heading_path": "/ 3. Python 演示 / 3.1 基础",
            "headings": ["/ 3. Python 演示 / 3.1 基础", "/ 3. Python 演示 / 3.2 流程"],
            "source_type": "tech_doc",
            "source_url": "https://docs.python.org/zh-cn/3.15/tutorial/introduction.html",
            "excerpt": "第一段摘要",
            "excerpts": ["第一段摘要", "第二段摘要", "第三段摘要"],
            "score": 0.094,
        }
    ]
    store.add_message("s1", "assistant", "回答正文 [来源1]", sources=merged)

    history = store.get_history("s1")
    assert history[0]["sources"] == merged


def test_history_verified_only_filters_unverified(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.add_message("s1", "user", "第一个问题", request_id="r1")
    store.add_message("s1", "assistant", "已校验回答", answer_status="verified", request_id="r1")
    store.add_message("s1", "user", "第二个问题", request_id="r2")
    store.add_message("s1", "assistant", "摘录回答", answer_status="extractive", request_id="r2")
    store.add_message("s1", "user", "第三个问题", request_id="r3")
    store.add_message("s1", "assistant", "通用回答", answer_status="unverified_general", request_id="r3")
    store.add_message("s1", "assistant", "拒答", answer_status="refused", request_id="r4")

    all_messages = store.get_history("s1", limit=20)
    assert len(all_messages) == 7

    verified = store.get_history("s1", limit=20, verified_only=True)
    assert [m["content"] for m in verified] == [
        "第一个问题", "已校验回答", "第二个问题", "摘录回答", "第三个问题",
    ]
    assert all(m["role"] == "user" or m["answer_status"] in ("verified", "extractive")
               for m in verified)


def test_retrieval_trace_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.add_retrieval_trace(
        "s1",
        request_id="req-1",
        question="ChromaDB 如何持久化？",
        complexity="simple",
        route="fallback",
        local_level=2,
        web_used=True,
        web_urls=["https://example.com/a"],
        answer_status="verified",
        verified=True,
        fallback_level=0,
        kb_written=True,
        elapsed_ms=1234,
    )
    trace = store.get_retrieval_trace("req-1")
    assert trace is not None
    assert trace["complexity"] == "simple"
    assert trace["route"] == "fallback"
    assert trace["local_level"] == 2
    assert trace["web_used"] is True
    assert trace["web_urls"] == ["https://example.com/a"]
    assert trace["answer_status"] == "verified"
    assert trace["verified"] is True
    assert trace["fallback_level"] == 0
    assert trace["kb_written"] is True
    assert trace["interrupted"] is False

    traces = store.get_retrieval_traces("s1")
    assert len(traces) == 1 and traces[0]["request_id"] == "req-1"
    assert store.get_retrieval_trace("missing") is None


def test_legacy_database_is_migrated(tmp_path):
    """旧库仅含基础 messages 列，打开后自动补 answer_status / request_id。"""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
        " role TEXT NOT NULL, content TEXT NOT NULL, sources_json TEXT, model TEXT,"
        " created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at)"
        " VALUES ('s1', 'assistant', '旧回答', '2025-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    store = SessionStore(db_path)
    columns = {
        row["name"]
        for row in store._conn().execute("PRAGMA table_info(messages)")  # noqa: SLF001
    }
    assert {"answer_status", "request_id"}.issubset(columns)

    history = store.get_history("s1")
    assert history[0]["content"] == "旧回答"
    assert history[0]["answer_status"] is None
    # 旧回答未标记 verified/extractive，不进入回流上下文
    assert store.get_history("s1", verified_only=True) == []

    store.add_message("s1", "assistant", "新回答", answer_status="verified")
    assert store.get_history("s1", verified_only=True)[-1]["content"] == "新回答"


def test_score_log_and_recent_questions(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.add_score_log("s1", "ChromaDB 怎么配置？", q_score=85, r_score=90, level=3, confidence=0.8, model="qwen2.5:7b")
    store.add_score_log("s1", "这个怎么弄？", q_score=40, r_score=30, level=1, confidence=0.2, model="qwen2.5:7b")

    assert len(store.recent_questions(days=7)) == 2
    assert store.recent_questions(days=0) == []


def test_feedback(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    store.add_feedback("s1", "这个问题没答案", kind="manual_review", note="占位")
    import sqlite3

    conn = sqlite3.connect(tmp_path / "s.db")
    row = conn.execute("SELECT kind, note FROM feedback").fetchone()
    conn.close()
    assert row == ("manual_review", "占位")
