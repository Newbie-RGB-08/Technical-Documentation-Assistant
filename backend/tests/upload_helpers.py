"""P4 上传测试共享夹具（真实 MySQL 测试库 + 临时 Chroma/上传目录）。"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.bm25_index import BM25Index
from app.routers import uploads
from app.services.asset_store import UploadAssetStore
from app.services.metadata_store import (
    Chunk,
    Document,
    MySQLMetadataStore,
    UploadAsset,
    UploadFile,
    UploadTask,
)
from app.services.upload_pipeline import UploadPipeline
from app.services.upload_store import UploadStore
from app.services.vector_store import ChromaStore
from tests.conftest import _ensure_test_db, _test_mysql_url


def cleanup_upload_data(metadata: MySQLMetadataStore, chroma: ChromaStore) -> None:
    with metadata.engine.begin() as conn:
        doc_ids = [
            row[0]
            for row in conn.execute(
                sa.text("SELECT id FROM documents WHERE source_type='uploaded_doc'")
            )
        ]
        if doc_ids:
            conn.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
            conn.execute(delete(Document).where(Document.id.in_(doc_ids)))
        for model in (UploadAsset, UploadFile, UploadTask):
            conn.execute(delete(model))
    try:
        chroma.delete_by_metadata("uploaded_doc", {"source_type": "uploaded_doc"})
    except Exception:  # noqa: BLE001 - 空集合/首次运行可忽略
        pass


class UploadEnv:
    def __init__(self, root: Path):
        _ensure_test_db()
        self.root = root
        self.chroma = ChromaStore(persist_path=str(root / "chroma"))
        self.metadata = MySQLMetadataStore(url=_test_mysql_url())
        cleanup_upload_data(self.metadata, self.chroma)
        self.upload_store = UploadStore(self.metadata.engine)
        self.asset_store = UploadAssetStore(root=root / "uploads")
        self.bm25 = BM25Index()
        self.state = SimpleNamespace(
            chroma=self.chroma,
            metadata=self.metadata,
            bm25=self.bm25,
            upload_store=self.upload_store,
            asset_store=self.asset_store,
        )
        self.pipeline = UploadPipeline(
            self.state,
            self.upload_store,
            self.asset_store,
            refresh_callback=self._refresh,
            max_workers=1,
        )
        self.state.upload_pipeline = self.pipeline
        app = FastAPI()
        app.include_router(uploads.router)
        app.state.core = self.state
        self.app = app
        self.client = TestClient(app)

    def _refresh(self, state) -> None:
        try:
            state.bm25.load_from_chroma(state.chroma)
        except Exception:  # noqa: BLE001 - 测试环境允许空索引
            pass

    def wait_task(self, task_id: str, timeout: float = 90.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self.client.get(f"/api/uploads/tasks/{task_id}")
            assert response.status_code == 200, response.text
            task = response.json()
            if task["status"] in ("completed", "partial", "failed"):
                return task
            time.sleep(0.2)
        raise AssertionError(f"任务未在 {timeout}s 内结束: {task_id}")

    def wait_file(self, upload_id: str, timeout: float = 90.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self.client.get(f"/api/uploads/{upload_id}")
            assert response.status_code == 200, response.text
            item = response.json()
            if item["status"] in (
                "indexed",
                "parsed",
                "failed_parse",
                "failed_index",
            ):
                return item
            time.sleep(0.2)
        raise AssertionError(f"文件未在 {timeout}s 内结束: {upload_id}")

    def close(self) -> None:
        self.pipeline.shutdown(wait=True)
        cleanup_upload_data(self.metadata, self.chroma)
        self.metadata.engine.dispose()
        self.chroma._client = None  # type: ignore[attr-defined]
