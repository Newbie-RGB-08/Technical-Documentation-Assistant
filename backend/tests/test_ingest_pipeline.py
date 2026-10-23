"""阶段 2 集成测试：增量入库管道（Chroma 临时目录 + MySQL 测试库）。

覆盖验收标准 #1/#2/#3：全量入库、重复跳过、单文件增量更新。
"""
import pathlib

import pytest

from app.services.ingest_pipeline import compute_content_hash, ingest_files
from app.services.metadata_store import MySQLMetadataStore
from app.services.note_scanner import normalize_file_path, scan_markdown_files
from app.services.vector_store import ChromaStore

NOTES = {
    "a_chroma.md": (
        "---\ntitle: Chroma 入门\ntags: [chroma]\n---\n\n"
        "# Chroma 入门\n\nPersistentClient 用于持久化存储。\n"
    ),
    "b_retrieval.md": (
        "# 检索策略\n\n混合检索结合向量与 BM25，RRF 融合两路结果。\n"
    ),
    "c_llm.md": "# LLM 网关\n\n统一网关支持 Ollama 与 OpenAI 兼容协议。\n",
}


@pytest.fixture(scope="module")
def stores(tmp_path_factory):
    chroma_dir = tmp_path_factory.mktemp("chroma")
    import sqlalchemy as sa
    from sqlalchemy import delete

    from app.config import get_settings
    from app.services.metadata_store import Chunk, Document, DocumentTag, IngestTask, Tag

    settings = get_settings()
    test_url = settings.mysql_url.replace("/rag_meta?", "/rag_meta_test?")
    admin = sa.create_engine(test_url.rsplit("/", 1)[0] + "/")
    with admin.connect() as conn:
        conn.execute(sa.text("CREATE DATABASE IF NOT EXISTS rag_meta_test CHARACTER SET utf8mb4"))
        conn.commit()

    metadata_store = MySQLMetadataStore(url=test_url)
    # 测试库跨 pytest 运行持久化；本模块用例开始前清空，保证可重复执行
    with metadata_store.engine.begin() as conn:
        for model in (DocumentTag, Chunk, Tag, IngestTask, Document):
            conn.execute(delete(model))
    return ChromaStore(persist_path=str(chroma_dir)), metadata_store


@pytest.fixture(scope="module")
def notes_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("notes")
    for name, text in NOTES.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def test_full_ingest_then_skip(stores, notes_dir):
    vector_store, metadata_store = stores
    files = scan_markdown_files(notes_dir)
    assert len(files) == 3

    summary = ingest_files(files, vector_store, metadata_store)
    assert summary["ingested"] == 3 and summary["failed"] == 0 and summary["total_chunks"] > 0

    # Chroma 与 MySQL chunk 数一致（验收 #1）
    total_in_mysql = sum(
        len(metadata_store.get_chunks_by_document(metadata_store.get_document(f["file_path"])["id"]))
        for f in files
    )
    assert vector_store.count("personal_note") == total_in_mysql

    # 重复入库全部跳过（验收 #2）
    summary2 = ingest_files(files, vector_store, metadata_store)
    assert summary2["skipped"] == 3 and summary2["ingested"] == 0


def test_incremental_update_single_file(stores, notes_dir):
    vector_store, metadata_store = stores
    target = notes_dir / "b_retrieval.md"
    old_doc = metadata_store.get_document(normalize_file_path(target))
    old_ids = {c["chroma_id"] for c in metadata_store.get_chunks_by_document(old_doc["id"])}

    # 修改一个文件（验收 #3）
    target.write_text(
        "# 检索策略 v2\n\n更新后的内容：加入了 Rerank 精排说明，RRF k=60。\n",
        encoding="utf-8",
    )
    files = scan_markdown_files(notes_dir)
    summary = ingest_files(files, vector_store, metadata_store)
    assert summary["ingested"] == 1 and summary["skipped"] == 2 and summary["failed"] == 0

    new_doc = metadata_store.get_document(normalize_file_path(target))
    assert new_doc["content_hash"] != old_doc["content_hash"]
    new_ids = {c["chroma_id"] for c in metadata_store.get_chunks_by_document(new_doc["id"])}
    assert new_ids != old_ids, "变更文件的向量 ID 应更新"

    # 未变更文件的 hash 不变
    other = metadata_store.get_document(normalize_file_path(notes_dir / "a_chroma.md"))
    assert other["content_hash"] == compute_content_hash(NOTES["a_chroma.md"].split("---\n\n", 1)[1])


def test_force_reingest(stores, notes_dir):
    vector_store, metadata_store = stores
    files = scan_markdown_files(notes_dir)
    summary = ingest_files(files, vector_store, metadata_store, force_reingest=True)
    assert summary["ingested"] == 3 and summary["skipped"] == 0


def test_vector_query_after_ingest(stores, notes_dir):
    vector_store, _ = stores
    results = vector_store.query("PersistentClient 持久化", top_k=3, source_type="personal_note")
    assert results, "入库后应能检索到内容"
    assert results[0]["score"] > 0.3
    assert "file_path" in results[0]["metadata"]


def test_single_file_failure_does_not_block_batch(stores, tmp_path):
    vector_store, metadata_store = stores
    bad = tmp_path / "bad.md"
    bad.write_text("# Bad\n\n内容", encoding="utf-8")
    good = tmp_path / "good.md"
    good.write_text("# Good\n\n正常内容", encoding="utf-8")

    files = scan_markdown_files(tmp_path)
    # 构造一个会失败的记录（非法 source_type）
    files_with_bad = [dict(f, source_type="invalid_type") if f["file_path"].endswith("bad.md") else f for f in files]
    summary = ingest_files(files_with_bad, vector_store, metadata_store)
    assert summary["failed"] == 1 and summary["ingested"] == 1
    assert summary["errors"][0]["file_path"].endswith("bad.md")


def test_prepare_chunks_carries_source_url():
    """prepare_chunks 把 source_url 透传到每个 chunk 的 metadata。"""
    from app.services.ingest_pipeline import prepare_chunks

    record = {
        "file_path": "/tmp/x.md",
        "source_type": "tech_doc",
        "title": "T",
        "tags": [],
        "content": "# 标题\n\n正文片段，正文片段，正文片段。",
        "source_url": "https://example.com/doc",
    }
    chunks = prepare_chunks(record, chunk_size=1000, chunk_overlap=0)
    assert chunks
    for c in chunks:
        assert c["metadata"]["source_url"] == "https://example.com/doc"
        assert c["metadata"]["source_type"] == "tech_doc"


def test_prepare_chunks_carries_web_metadata():
    """网页写回的新元数据透传到每个 chunk（E.10.2）。"""
    from app.services.ingest_pipeline import prepare_chunks

    record = {
        "file_path": "/tmp/web.md",
        "source_type": "tech_doc",
        "title": "T",
        "tags": ["web"],
        "content": "# 标题\n\n网页正文片段。",
        "source_url": "https://example.com/doc",
        "canonical_url": "https://example.com/doc",
        "origin": "web_search",
        "retrieved_at": "2026-03-01T10:00:00+08:00",
        "expires_at": "2026-03-31T10:00:00+08:00",
        "search_query": "示例查询",
        "provider": "tavily",
    }
    chunks = prepare_chunks(record, chunk_size=1000, chunk_overlap=0)
    assert chunks
    for c in chunks:
        meta = c["metadata"]
        assert meta["origin"] == "web_search"
        assert meta["canonical_url"] == "https://example.com/doc"
        assert meta["retrieved_at"] == "2026-03-01T10:00:00+08:00"
        assert meta["expires_at"] == "2026-03-31T10:00:00+08:00"
        assert meta["search_query"] == "示例查询"
        assert meta["provider"] == "tavily"


def test_ingest_calls_on_file_ingested_callback(stores, tmp_path):
    vector_store, metadata_store = stores
    p = tmp_path / "callback.md"
    p.write_text("# 回调\n\n写回后需要刷新索引。", encoding="utf-8")
    files = scan_markdown_files(tmp_path)
    events = []
    summary = ingest_files(
        files, vector_store, metadata_store,
        force_reingest=True, on_file_ingested=events.append,
    )
    assert summary["ingested"] == 1
    assert len(events) == 1
    assert events[0]["file_path"] == normalize_file_path(p)
    assert events[0]["source_type"] == "personal_note"
    assert events[0]["chunk_count"] > 0
    assert events[0]["document_id"] > 0


def test_prepare_chunks_default_output_is_unchanged():
    """P3 回归：不传 chunker 时必须与旧 chunk_markdown 输出逐字段一致。"""
    from app.core.chunker import chunk_markdown
    from app.services.ingest_pipeline import prepare_chunks, sanitize_metadata

    record = {
        "file_path": "notes/regression.md",
        "source_type": "personal_note",
        "title": "回归",
        "tags": [],
        "content": "# 回归\n\n正文内容。",
    }
    file_meta = {
        "file_path": "notes/regression.md",
        "source_type": "personal_note",
        "title": "回归",
        "tags": [],
        "created_at": "",
        "updated_at": "",
        "source_url": "",
        "canonical_url": "",
        "origin": "",
        "retrieved_at": "",
        "expires_at": "",
        "search_query": "",
        "provider": "",
        "content_hash": compute_content_hash(record["content"]),
    }
    expected = chunk_markdown(record["content"], file_meta)
    for i, chunk in enumerate(expected):
        chunk["metadata"]["global_index"] = i
        chunk["metadata"] = sanitize_metadata(chunk["metadata"])
    assert prepare_chunks(record) == expected


def test_prepare_chunks_rich_chunker_and_extra_metadata():
    from app.core.rich_chunker import chunk_uploaded_document
    from app.services.ingest_pipeline import prepare_chunks

    record = {
        "file_path": "uploads/u_p3/parsed.md",
        "source_type": "uploaded_doc",
        "title": "上传表格",
        "tags": [],
        "content": "# 表格\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n",
    }
    chunks = prepare_chunks(
        record,
        chunk_size=1000,
        chunk_overlap=0,
        chunker=chunk_uploaded_document,
        extra_metadata={
            "upload_id": "u_p3",
            "doc_format": "pdf",
            "file_path": "should-not-override",
            "source_type": "tech_doc",
            "title": "should-not-override",
            "global_index": 999,
        },
    )
    assert chunks
    table = [c for c in chunks if c["metadata"].get("content_type") == "table"]
    assert table
    meta = table[0]["metadata"]
    assert meta["upload_id"] == "u_p3"
    assert meta["doc_format"] == "pdf"
    assert meta["file_path"] == record["file_path"]
    assert meta["source_type"] == "uploaded_doc"
    assert meta["title"] == "上传表格"
    assert all(c["metadata"]["global_index"] == i for i, c in enumerate(chunks))
    assert meta["global_index"] != 999


def test_ingest_files_accepts_rich_chunker_and_upload_metadata(stores, tmp_path):
    from app.core.rich_chunker import chunk_uploaded_document
    from app.services.ingest_pipeline import ingest_files

    vector_store, metadata_store = stores
    p = tmp_path / "uploaded_table.md"
    p.write_text(
        "# 上传文档\n\n| 名称 | 数量 |\n| --- | --- |\n| 苹果 | 3 |\n",
        encoding="utf-8",
    )
    record = {
        "file_path": str(p).replace("\\", "/"),
        "source_type": "uploaded_doc",
        "title": "上传文档",
        "tags": [],
        "content": p.read_text(encoding="utf-8"),
    }
    try:
        summary = ingest_files(
            [record],
            vector_store,
            metadata_store,
            force_reingest=True,
            chunker=chunk_uploaded_document,
            extra_metadata={
                "upload_id": "u_p3_ingest",
                "doc_format": "md",
                "original_filename": "uploaded_table.md",
            },
        )
        assert summary["ingested"] == 1 and summary["failed"] == 0
        hits = vector_store.query("苹果", source_type="uploaded_doc", top_k=5)
        assert hits
        assert hits[0]["metadata"]["upload_id"] == "u_p3_ingest"
        assert hits[0]["metadata"]["doc_format"] == "md"
        doc = metadata_store.get_document(record["file_path"])
        assert doc and doc["source_type"] == "uploaded_doc"
    finally:
        vector_store.delete_by_metadata("uploaded_doc", {"file_path": record["file_path"]})
        metadata_store.delete_document(record["file_path"])


def test_ingest_writes_source_url_to_document_and_chunks(stores, tmp_path):
    """完整入库流程把 source_url 同时写进 MySQL documents 与 Chroma chunk metadata。"""
    from app.services.ingest_pipeline import ingest_files

    fm = (
        "---\n"
        "title: FastAPI Depends 实战\n"
        "source_url: https://fastapi.tiangolo.com/tutorial/dependencies/\n"
        "created_at: 2026-01-01\n"
        "updated_at: 2026-01-01\n"
        "---\n\n"
        "# FastAPI Depends\n\n"
        "依赖注入的正文内容。"
    )
    p = tmp_path / "depends.md"
    p.write_text(fm, encoding="utf-8")
    files = scan_markdown_files(tmp_path, source_type="tech_doc")
    files[0]["source_url"] = "https://fastapi.tiangolo.com/tutorial/dependencies/"

    vector_store, metadata_store = stores
    summary = ingest_files(files, vector_store, metadata_store, force_reingest=True)
    assert summary["ingested"] == 1

    # MySQL 文档记录
    doc = metadata_store.get_document(normalize_file_path(p))
    assert doc is not None
    assert doc.get("source_url") == "https://fastapi.tiangolo.com/tutorial/dependencies/"

    # Chroma chunk metadata
    results = vector_store.query("Depends 依赖注入", source_type="tech_doc", top_k=3)
    # (Chroma 可能还被其他测试占着，只要能找到我们的文件就行)
    our = [r for r in results if r["metadata"].get("file_path") == normalize_file_path(p)]
    assert our, "Chroma 未找到我们的测试文档"
    for c in our:
        assert c["metadata"].get("source_url") == "https://fastapi.tiangolo.com/tutorial/dependencies/"
