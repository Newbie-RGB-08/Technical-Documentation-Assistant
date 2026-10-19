"""V1.1 新增：rag_service.build_sources / build_context 字段完整性。"""
from app.services.rag_service import build_context, build_sources


_CHUNKS = [
    {
        "id": "a.md#0",
        "text": "ChromaDB 的 PersistentClient 用持久化路径启动。",
        "metadata": {
            "file_path": "D:/docs/chromadb_intro.md",
            "source_type": "tech_doc",
            "title": "ChromaDB 入门",
            "heading_path": "/ 基础 / 持久化",
            "source_url": "https://example.com/chromadb",
        },
        "rerank_score": 0.95,
    },
    {
        "id": "note.md#0",
        "text": "这是一条本地笔记片段，没有 source_url。",
        "metadata": {
            "file_path": "D:/notes/note.md",
            "source_type": "personal_note",
            "title": "随手记",
            "heading_path": "",
            "source_url": "",
        },
        "rerank_score": 0.80,
    },
]


def test_build_sources_returns_all_new_fields():
    out = build_sources(_CHUNKS)
    assert len(out) == 2

    a = out[0]
    assert a["index"] == 1
    assert a["title"] == "ChromaDB 入门"
    assert a["file_path"] == "D:/docs/chromadb_intro.md"
    assert a["heading_path"] == "/ 基础 / 持久化"
    assert a["source_type"] == "tech_doc"
    assert a["source_url"] == "https://example.com/chromadb"
    assert a["score"] == 0.95
    assert a["excerpt"].startswith("ChromaDB 的 PersistentClient")


def test_build_sources_source_url_default_empty():
    """缺 source_url 的 chunk → 默认空串（不要 None / 不要漏字段）。"""
    out = build_sources(_CHUNKS)
    b = out[1]
    assert b["source_url"] == ""
    assert b["source_type"] == "personal_note"
    assert b["title"] == "随手记"


def test_build_context_injects_source_url_for_web_chunks():
    ctx = build_context(_CHUNKS)
    # 网页来源的 chunk，LLM 提示里必须带 "网页来源：URL"
    assert "网页来源：https://example.com/chromadb" in ctx
    # 本地笔记的 chunk 不带多余的来源行，避免干扰
    assert "本地笔记片段" in ctx
    assert "| 网页来源：" not in ctx.split("[来源2]")[1].split("\n")[0]
    # 序号 + 文件路径 + 章节必须存在
    assert "[来源1]" in ctx and "[来源2]" in ctx
    assert "D:/docs/chromadb_intro.md" in ctx
    assert "/ 基础 / 持久化" in ctx
