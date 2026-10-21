"""阶段 2 测试：MySQL 元数据存储（连接测试库 rag_meta_test）。"""
import sqlalchemy as sa
from sqlalchemy import delete

from app.config import get_settings
from app.services.metadata_store import (
    Chunk,
    Document,
    DocumentTag,
    IngestTask,
    MySQLMetadataStore,
    Tag,
)


def _store() -> MySQLMetadataStore:
    settings = get_settings()
    test_url = settings.mysql_url.replace("/rag_meta?", "/rag_meta_test?")
    # 确保测试库存在
    admin = sa.create_engine(test_url.rsplit("/", 1)[0] + "/")
    with admin.connect() as conn:
        conn.execute(sa.text("CREATE DATABASE IF NOT EXISTS rag_meta_test CHARACTER SET utf8mb4"))
        conn.commit()
    store = MySQLMetadataStore(url=test_url)
    # 测试库跨 pytest 运行持久化；每个用例开始前清空，保证测试可重复执行
    with store.engine.begin() as conn:
        for model in (DocumentTag, Chunk, Tag, IngestTask, Document):
            conn.execute(delete(model))
    return store


def test_document_upsert_get_delete():
    store = _store()
    doc_id = store.upsert_document(
        {
            "file_path": "test_docs/a.md",
            "source_type": "personal_note",
            "title": "A",
            "content_hash": "0" * 64,
            "chunk_count": 3,
        }
    )
    assert doc_id > 0

    fetched = store.get_document("test_docs/a.md")
    assert fetched and fetched["title"] == "A" and fetched["chunk_count"] == 3

    # upsert 更新
    store.upsert_document(
        {
            "file_path": "test_docs/a.md",
            "source_type": "personal_note",
            "title": "A2",
            "content_hash": "1" * 64,
            "chunk_count": 5,
        }
    )
    assert store.get_document("test_docs/a.md")["title"] == "A2"
    assert store.get_document("test_docs/a.md")["content_hash"] == "1" * 64

    assert store.delete_document("test_docs/a.md") == doc_id
    assert store.get_document("test_docs/a.md") is None
    assert store.delete_document("test_docs/a.md") == -1


def test_get_document_by_source_url():
    store = _store()
    store.upsert_document(
        {
            "file_path": "test_docs/web-page.md",
            "source_type": "tech_doc",
            "title": "Web Page",
            "source_url": "https://example.com/doc",
            "content_hash": "9" * 64,
            "chunk_count": 2,
        }
    )
    found = store.get_document_by_source_url("https://example.com/doc")
    assert found and found["file_path"] == "test_docs/web-page.md"
    assert found["source_url"] == "https://example.com/doc"
    assert store.get_document_by_source_url("https://example.com/other") is None
    assert store.get_document_by_source_url("") is None


def test_chunks_mapping_roundtrip():
    store = _store()
    doc_id = store.upsert_document(
        {
            "file_path": "test_docs/b.md",
            "source_type": "tech_doc",
            "title": "B",
            "content_hash": "2" * 64,
        }
    )
    chunks = [
        {"text": "t1", "metadata": {"global_index": 0, "heading_path": "H1"}},
        {"text": "t2", "metadata": {"global_index": 1, "heading_path": "H2"}},
    ]
    store.save_chunks(doc_id, chunks, ["id0", "id1"])
    rows = store.get_chunks_by_document(doc_id)
    assert len(rows) == 2
    assert {r["chroma_id"] for r in rows} == {"id0", "id1"}

    # 重复 save 覆盖旧行
    store.save_chunks(doc_id, chunks[:1], ["new0"])
    rows = store.get_chunks_by_document(doc_id)
    assert len(rows) == 1 and rows[0]["chroma_id"] == "new0"

    # 级联删除
    store.delete_document("test_docs/b.md")
    assert store.get_chunks_by_document(doc_id) == []


def test_tags():
    store = _store()
    doc_id = store.upsert_document(
        {
            "file_path": "test_docs/c.md",
            "source_type": "personal_note",
            "content_hash": "3" * 64,
        }
    )
    store.save_tags(doc_id, ["chroma", "rag"])
    store.save_tags(doc_id, ["chroma", "检索"])  # 覆盖
    import sqlalchemy as sa2

    with sa2.create_engine(store.engine.url).connect() as conn:
        names = [
            r[0]
            for r in conn.execute(
                sa2.text(
                    "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id=t.id"
                    " WHERE dt.document_id=:d"
                ),
                {"d": doc_id},
            )
        ]
    assert sorted(names) == ["chroma", "检索"]


def test_task_crud():
    store = _store()
    store.create_task("task-1", file_count=5)
    task = store.get_task("task-1")
    assert task and task["status"] == "pending" and task["file_count"] == 5

    store.update_task("task-1", status="completed", chunk_count=42)
    task = store.get_task("task-1")
    assert task["status"] == "completed" and task["chunk_count"] == 42
    assert task["completed_at"] is not None
