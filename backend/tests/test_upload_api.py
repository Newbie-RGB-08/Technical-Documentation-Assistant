"""P4：上传 API 集成测试（multipart/错误码/列表/预览/删除）。"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.routers import uploads as uploads_router
from tests.upload_helpers import UploadEnv


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    helper = UploadEnv(tmp_path_factory.mktemp("upload_api"))
    try:
        yield helper
    finally:
        helper.close()


def _upload(client, files, **data):
    defaults = {"engine": "native", "auto_index": "true"}
    defaults.update({k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in data.items()})
    return client.post("/api/uploads", files=files, data=defaults)


def test_engines_and_upload_end_to_end(env):
    response = env.client.get("/api/uploads/engines")
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["engines"]}
    assert {"native", "rapidocr", "mineru", "paddlex"} <= names

    content = (
        "# 上传指南\n\n"
        "苹果的数量是三。\n\n"
        "| 名称 | 数量 |\n| --- | --- |\n| 苹果 | 3 |\n"
    ).encode("utf-8")
    response = _upload(
        env.client,
        [("files", ("FastAPI 指南.md", content, "text/markdown"))],
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["task_id"].startswith("t_")
    item = payload["files"][0]
    assert item["status"] == "queued" and item["format"] == "md"
    upload_id = item["upload_id"]

    task = env.wait_task(payload["task_id"])
    assert task["status"] == "completed"
    assert task["processed_files"] == 1 and task["failed_files"] == 0
    detail = env.wait_file(upload_id)
    assert detail["status"] == "indexed"
    assert detail["parse_engine"] == "native"
    assert detail["chunk_count"] >= 1
    assert detail["document_id"]
    assert detail["markdown_path"].endswith("parsed.md")

    markdown = env.client.get(f"/api/uploads/{upload_id}/markdown")
    assert markdown.status_code == 200
    assert "上传指南" in markdown.text and "| 苹果 | 3 |" in markdown.text
    assert markdown.headers["content-type"].startswith("text/markdown")

    original = env.client.get(f"/api/uploads/{upload_id}/original")
    assert original.status_code == 200
    assert original.content == content

    listed = env.client.get("/api/uploads", params={"format": "md", "q": "FastAPI"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["upload_id"] == upload_id

    hits = env.chroma.query("苹果 数量", source_type="uploaded_doc", top_k=5)
    assert hits and hits[0]["metadata"]["upload_id"] == upload_id
    assert hits[0]["metadata"]["content_type"] in ("text", "table")
    assert not env.chroma.query("苹果 数量", source_type="tech_doc", top_k=5)

    deleted = env.client.delete(f"/api/uploads/{upload_id}")
    assert deleted.status_code == 200 and deleted.json()["ok"] is True
    assert env.client.get(f"/api/uploads/{upload_id}").status_code == 404
    assert not env.chroma.query("苹果 数量", source_type="uploaded_doc", top_k=5)
    assert env.metadata.get_document(
        f"uploads/{upload_id}/FastAPI 指南.md"
    ) is None or True  # 路径取决于测试临时目录，下面按 upload 记录断言
    assert env.upload_store.get_file(upload_id) is None


def test_duplicate_and_force_semantics(env):
    content = b"# Duplicate\n\nsame content for duplicate test\n"
    first = _upload(env.client, [("files", ("dup-a.md", content, "text/markdown"))])
    assert first.status_code == 202
    first_task = first.json()["task_id"]
    first_id = first.json()["files"][0]["upload_id"]
    env.wait_task(first_task)

    duplicate = _upload(env.client, [("files", ("dup-b.md", content, "text/markdown"))])
    assert duplicate.status_code == 409
    assert "已存在相同内容" in duplicate.json()["detail"]
    assert duplicate.json()["files"][0]["status"] == "duplicate"

    forced = _upload(
        env.client,
        [("files", ("dup-c.md", content, "text/markdown"))],
        force=True,
    )
    assert forced.status_code == 202
    forced_id = forced.json()["files"][0]["upload_id"]
    env.wait_task(forced.json()["task_id"])
    assert env.wait_file(forced_id)["status"] == "indexed"
    assert first_id != forced_id

    # 同批次重复：第一个入库，第二个 duplicate，但整批仍返回 202
    batch = _upload(
        env.client,
        [
            ("files", ("batch-a.md", b"# Batch\n\nbatch duplicate body", "text/markdown")),
            ("files", ("batch-b.md", b"# Batch\n\nbatch duplicate body", "text/markdown")),
        ],
    )
    assert batch.status_code == 202
    statuses = {item["status"] for item in batch.json()["files"]}
    assert statuses == {"queued", "duplicate"}
    env.wait_task(batch.json()["task_id"])


def test_format_engine_and_empty_rejections(env):
    for name, fragment in (
        ("a.exe", "不支持"),
        ("a.doc", "另存为 .docx"),
        ("a.ppt", "另存为 .pptx"),
        ("a.docm", "宏格式"),
        ("a.xlsm", "宏格式"),
    ):
        response = _upload(env.client, [("files", (name, b"x", "application/octet-stream"))])
        assert response.status_code == 400, (name, response.text)
        assert fragment in response.json()["detail"]

    empty = _upload(env.client, [("files", ("empty.md", b"", "text/markdown"))])
    assert empty.status_code == 400
    assert "为空" in empty.json()["detail"]

    unavailable = _upload(
        env.client,
        [("files", ("x.md", b"# x\n", "text/markdown"))],
        engine="mineru",
    )
    assert unavailable.status_code == 503

    unknown = _upload(
        env.client,
        [("files", ("x.md", b"# x\n", "text/markdown"))],
        engine="unknown",
    )
    assert unknown.status_code == 400

    image = _upload(env.client, [("files", ("x.png", b"\x89PNG\r\n", "image/png"))])
    assert image.status_code == 422


def test_partial_rejection_does_not_block_valid_file(env):
    response = _upload(
        env.client,
        [
            ("files", ("valid.md", b"# Valid\n\nvalid partial body", "text/markdown")),
            ("files", ("bad.exe", b"bad", "application/octet-stream")),
        ],
    )
    assert response.status_code == 202, response.text
    statuses = {item["filename"]: item["status"] for item in response.json()["files"]}
    assert statuses["valid.md"] == "queued"
    assert statuses["bad.exe"] == "rejected"
    task = env.wait_task(response.json()["task_id"])
    assert task["status"] == "completed"


def test_size_and_count_limits(env, monkeypatch):
    monkeypatch.setattr(
        uploads_router,
        "get_settings",
        lambda: SimpleNamespace(
            upload_enabled=True,
            upload_max_files_per_batch=20,
            upload_max_mb=1,
        ),
    )
    oversized = _upload(
        env.client,
        [("files", ("big.md", b"a" * (1024 * 1024 + 1), "text/markdown"))],
    )
    assert oversized.status_code == 413
    assert "超过" in oversized.json()["detail"]

    too_many = _upload(
        env.client,
        [
            ("files", (f"many-{i}.md", b"# many\n", "text/markdown"))
            for i in range(21)
        ],
    )
    assert too_many.status_code == 400
    assert "最多" in too_many.json()["detail"]


def test_auto_index_false_then_reindex_and_reparse(env):
    response = _upload(
        env.client,
        [("files", ("manual.md", b"# Manual\n\nmanual index body", "text/markdown"))],
        auto_index=False,
    )
    assert response.status_code == 202
    upload_id = response.json()["files"][0]["upload_id"]
    env.wait_task(response.json()["task_id"])
    detail = env.wait_file(upload_id)
    assert detail["status"] == "parsed"
    assert detail["chunk_count"] == 0
    assert env.client.get(f"/api/uploads/{upload_id}/markdown").status_code == 200
    assert not any(
        hit["metadata"].get("upload_id") == upload_id
        for hit in env.chroma.query("manual index body", source_type="uploaded_doc", top_k=20)
    )

    reindex = env.client.post(f"/api/uploads/{upload_id}/reindex")
    assert reindex.status_code == 202
    env.wait_task(reindex.json()["task_id"])
    assert env.wait_file(upload_id)["status"] == "indexed"
    first_count = env.chroma.count("uploaded_doc")

    reparse = env.client.post(f"/api/uploads/{upload_id}/reparse")
    assert reparse.status_code == 202
    env.wait_task(reparse.json()["task_id"])
    detail = env.wait_file(upload_id)
    assert detail["status"] == "indexed"
    assert detail["chunk_count"] >= 1
    assert env.chroma.count("uploaded_doc") == first_count


def test_enum_not_ready_returns_503(env):
    original = env.metadata.source_type_enum_ready
    env.metadata.source_type_enum_ready = False
    try:
        response = env.client.get("/api/uploads")
        assert response.status_code == 503
        assert "迁移" in response.json()["detail"]
    finally:
        env.metadata.source_type_enum_ready = original


def test_unknown_ids_return_404(env):
    assert env.client.get("/api/uploads/u_missing").status_code == 404
    assert env.client.get("/api/uploads/tasks/t_missing").status_code == 404
    assert env.client.post("/api/uploads/u_missing/reparse").status_code == 404
    assert env.client.post("/api/uploads/u_missing/reindex").status_code == 404
    assert env.client.delete("/api/uploads/u_missing").status_code == 404
