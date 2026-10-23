"""P4：上传解析/切片/索引/删除闭环与状态机。"""
from __future__ import annotations

import io
import uuid

import pytest

from app.services.note_scanner import normalize_file_path
from tests.upload_helpers import UploadEnv


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    helper = UploadEnv(tmp_path_factory.mktemp("upload_pipeline"))
    try:
        yield helper
    finally:
        helper.close()


def _queued_file(env: UploadEnv, name: str, content: bytes, *, auto_index=True):
    upload_id = f"u_pipe_{uuid.uuid4().hex[:12]}"
    saved = env.asset_store.save_stream(upload_id, name, io.BytesIO(content))
    task_id = f"t_pipe_{uuid.uuid4().hex[:12]}"
    fmt = name.rsplit(".", 1)[-1].lower()
    env.upload_store.create_task(task_id, engine="native", total_files=1)
    env.upload_store.create_file(
        upload_id,
        name,
        saved["stored_path"],
        fmt,
        saved["file_size"],
        saved["content_hash"],
        task_id=task_id,
        auto_index=auto_index,
        chunk_size=512,
        chunk_overlap=64,
    )
    return task_id, upload_id, saved


def test_upload_parse_chunk_index_bm25_and_delete(env):
    content = (
        "# 盘古上传文档\n\n"
        "盘古计划包含独特的量子检索术语。\n\n"
        "| 模块 | 数量 |\n| --- | --- |\n| 检索 | 3 |\n"
    ).encode("utf-8")
    task_id, upload_id, saved = _queued_file(env, "盘古.md", content)
    env.pipeline.process_batch(task_id)

    task = env.upload_store.get_task(task_id)
    assert task["status"] == "completed"
    assert task["processed_files"] == 1
    record = env.upload_store.get_file(upload_id)
    assert record["status"] == "indexed"
    assert record["parse_engine"] == "native"
    assert record["chunk_count"] >= 2
    assert record["document_id"]

    file_path = normalize_file_path(saved["stored_path"])
    doc = env.metadata.get_document(file_path)
    assert doc and doc["source_type"] == "uploaded_doc"
    chunks = env.metadata.get_chunks_by_document(doc["id"])
    assert len(chunks) == doc["chunk_count"] == record["chunk_count"]

    hits = env.chroma.query("盘古 量子检索", source_type="uploaded_doc", top_k=5)
    assert hits
    meta = hits[0]["metadata"]
    assert meta["upload_id"] == upload_id
    assert meta["source_type"] == "uploaded_doc"
    assert meta["file_path"] == file_path
    assert "heading_path" in meta and "content_type" in meta
    assert not env.chroma.query("盘古 量子检索", source_type="tech_doc", top_k=5)

    bm25_hits = env.bm25.search("盘古 量子", top_k=5)
    assert bm25_hits and any("盘古" in h["text"] for h in bm25_hits)

    assert env.client.get(f"/api/uploads/{upload_id}/markdown").status_code == 200
    env.pipeline.delete(upload_id)
    assert env.upload_store.get_file(upload_id) is None
    assert env.metadata.get_document(file_path) is None
    assert not env.chroma.query("盘古 量子检索", source_type="uploaded_doc", top_k=5)
    assert not (env.asset_store.upload_dir(upload_id)).exists()


def test_reindex_is_idempotent_and_updates_counts(env):
    task_id, upload_id, _ = _queued_file(
        env, "reindex.md", b"# Reindex\n\nrebuild unique index body"
    )
    env.pipeline.process_batch(task_id)
    before = env.upload_store.get_file(upload_id)
    assert before["status"] == "indexed"

    new_task = f"t_pipe_{uuid.uuid4().hex[:12]}"
    env.upload_store.create_task(new_task, engine="native", total_files=1)
    env.pipeline.prepare_reindex(upload_id, new_task)
    env.pipeline.process_batch(new_task)
    after = env.upload_store.get_file(upload_id)
    assert after["status"] == "indexed"
    assert after["chunk_count"] == before["chunk_count"]
    assert env.upload_store.get_task(new_task)["status"] == "completed"

    env.pipeline.delete(upload_id)


def test_auto_index_false_leaves_parsed_without_vectors(env):
    task_id, upload_id, _ = _queued_file(
        env, "parsed-only.md", b"# Parsed\n\nnot indexed body", auto_index=False
    )
    env.pipeline.process_batch(task_id)
    record = env.upload_store.get_file(upload_id)
    assert record["status"] == "parsed"
    assert record["chunk_count"] == 0
    assert env.upload_store.get_task(task_id)["status"] == "completed"
    assert not env.chroma.query("not indexed body", source_type="uploaded_doc", top_k=5)
    assert env.asset_store.markdown_path(upload_id).exists()
    env.pipeline.delete(upload_id)


def test_failure_states_and_partial_batch(env):
    missing_path = env.asset_store.root / "u_pipe_missing" / "missing.md"
    task_id = f"t_pipe_{uuid.uuid4().hex[:12]}"
    env.upload_store.create_task(task_id, engine="native", total_files=2)
    env.upload_store.create_file(
        "u_pipe_missing",
        "missing.md",
        str(missing_path),
        "md",
        10,
        "f" * 64,
        task_id=task_id,
    )
    valid_id = f"u_pipe_{uuid.uuid4().hex[:12]}"
    saved = env.asset_store.save_stream(
        valid_id, "valid.md", io.BytesIO(b"# Valid\n\npartial valid")
    )
    env.upload_store.create_file(
        valid_id,
        "valid.md",
        saved["stored_path"],
        "md",
        saved["file_size"],
        saved["content_hash"],
        task_id=task_id,
    )
    env.pipeline.process_batch(task_id)
    assert env.upload_store.get_file("u_pipe_missing")["status"] == "failed_parse"
    assert env.upload_store.get_file(valid_id)["status"] == "indexed"
    task = env.upload_store.get_task(task_id)
    assert task["status"] == "partial"
    assert task["failed_files"] == 1 and task["processed_files"] == 2
    env.pipeline.delete(valid_id)
    env.upload_store.delete_file("u_pipe_missing")


def test_unsupported_format_fails_cleanly(env):
    task_id, upload_id, _ = _queued_file(env, "bad.exe", b"not allowed")
    env.pipeline.process_batch(task_id)
    record = env.upload_store.get_file(upload_id)
    assert record["status"] == "failed_parse"
    assert "不支持" in record["error_message"]
    assert env.upload_store.get_task(task_id)["status"] == "failed"
    env.pipeline.delete(upload_id)
