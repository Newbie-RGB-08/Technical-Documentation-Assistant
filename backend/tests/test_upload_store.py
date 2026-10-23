"""P1：上传任务/文件/资源表 CRUD 与去重查询。"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import delete

from app.config import get_settings
from app.services.metadata_store import (
    MySQLMetadataStore,
    UploadAsset,
    UploadFile,
    UploadTask,
)
from app.services.upload_store import UploadStore


def _make_stores() -> tuple[MySQLMetadataStore, UploadStore]:
    settings = get_settings()
    url = settings.mysql_url.replace("/rag_meta?", "/rag_meta_test?")
    admin = sa.create_engine(url.rsplit("/", 1)[0] + "/")
    with admin.begin() as conn:
        conn.execute(
            sa.text("CREATE DATABASE IF NOT EXISTS rag_meta_test CHARACTER SET utf8mb4")
        )
    admin.dispose()
    metadata = MySQLMetadataStore(url=url)
    with metadata.engine.begin() as conn:
        for model in (UploadAsset, UploadFile, UploadTask):
            conn.execute(delete(model))
    return metadata, UploadStore(metadata.engine)


def test_upload_task_and_file_crud():
    metadata, uploads = _make_stores()
    try:
        task = uploads.create_task("task-a", engine="auto", total_files=2)
        assert task["status"] == "queued" and task["total_files"] == 2

        file1 = uploads.create_file(
            "u_a1",
            "说明 文档.pdf",
            "/uploads/u_a1/original.pdf",
            "pdf",
            1234,
            "c" * 64,
            task_id="task-a",
        )
        assert file1["upload_id"] == "u_a1" and file1["status"] == "queued"
        assert file1["auto_index"] is True and file1["chunk_size"] == 512

        items, total = uploads.list_files(task_id="task-a")
        assert total == 1 and items[0]["original_filename"] == "说明 文档.pdf"

        updated = uploads.update_file(
            "u_a1", status="ready", progress=100, chunk_count=7, parse_engine="native"
        )
        assert updated and updated["status"] == "ready" and updated["chunk_count"] == 7

        task2 = uploads.update_task(
            "task-a", status="completed", processed_files=1, failed_files=0
        )
        assert task2 and task2["status"] == "completed" and task2["completed_at"]

        assert uploads.find_by_hash("c" * 64)["upload_id"] == "u_a1"
        assert uploads.find_by_hash("d" * 64) is None
    finally:
        with metadata.engine.begin() as conn:
            for model in (UploadAsset, UploadFile, UploadTask):
                conn.execute(delete(model))
        metadata.engine.dispose()


def test_upload_assets_and_delete_cascade():
    metadata, uploads = _make_stores()
    try:
        uploads.create_file(
            "u_assets", "a.png", "/uploads/u_assets/original.png", "png", 10, "e" * 64
        )
        uploads.replace_assets(
            "u_assets",
            [
                {
                    "asset_type": "image",
                    "name": "image_001.png",
                    "rel_path": "assets/image_001.png",
                    "page_no": 2,
                    "caption": "示意图",
                    "ocr_text": "图内文字",
                },
                {
                    "kind": "table",
                    "name": "table_001.md",
                    "rel_path": "assets/table_001.md",
                },
            ],
        )
        assets = uploads.list_assets("u_assets")
        assert len(assets) == 2
        assert assets[0]["asset_type"] == "image" and assets[0]["page_no"] == 2
        assert assets[1]["asset_type"] == "table"

        # 覆盖写：旧资源不能残留
        uploads.replace_assets("u_assets", [])
        assert uploads.list_assets("u_assets") == []

        uploads.replace_assets(
            "u_assets",
            [{"asset_type": "image", "name": "x.png", "rel_path": "assets/x.png"}],
        )
        assert uploads.delete_file("u_assets")["upload_id"] == "u_assets"
        assert uploads.get_file("u_assets") is None
        assert uploads.list_assets("u_assets") == []
    finally:
        with metadata.engine.begin() as conn:
            for model in (UploadAsset, UploadFile, UploadTask):
                conn.execute(delete(model))
        metadata.engine.dispose()
