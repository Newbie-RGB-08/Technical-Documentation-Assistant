"""阶段 2 集成测试：增量入库管道（Chroma 临时目录 + MySQL 测试库）。

覆盖验收标准 #1/#2/#3：全量入库、重复跳过、单文件增量更新。
"""
import pathlib

import pytest

from app.services.ingest_pipeline import compute_content_hash, ingest_files
from app.services.metadata_store import MySQLMetadataStore
from app.services.note_scanner import scan_markdown_files
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
    old_doc = metadata_store.get_document(str(target))
    old_ids = {c["chroma_id"] for c in metadata_store.get_chunks_by_document(old_doc["id"])}

    # 修改一个文件（验收 #3）
    target.write_text(
        "# 检索策略 v2\n\n更新后的内容：加入了 Rerank 精排说明，RRF k=60。\n",
        encoding="utf-8",
    )
    files = scan_markdown_files(notes_dir)
    summary = ingest_files(files, vector_store, metadata_store)
    assert summary["ingested"] == 1 and summary["skipped"] == 2 and summary["failed"] == 0

    new_doc = metadata_store.get_document(str(target))
    assert new_doc["content_hash"] != old_doc["content_hash"]
    new_ids = {c["chroma_id"] for c in metadata_store.get_chunks_by_document(new_doc["id"])}
    assert new_ids != old_ids, "变更文件的向量 ID 应更新"

    # 未变更文件的 hash 不变
    other = metadata_store.get_document(str(notes_dir / "a_chroma.md"))
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
