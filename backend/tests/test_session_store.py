"""阶段 2 测试：SQLite 会话存储。"""
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
