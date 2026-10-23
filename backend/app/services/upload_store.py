"""上传任务/文件/资源元数据 CRUD（PRD 7.1.3）。

与 ``MySQLMetadataStore`` 共用 SQLAlchemy engine 和 ORM Base；上传路由、
异步执行器与前端列表都通过本模块访问，避免把上传状态散落到业务代码中。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.services.metadata_store import UploadAsset, UploadFile, UploadTask

_FILE_FIELDS = {
    "task_id",
    "original_filename",
    "stored_path",
    "markdown_path",
    "doc_format",
    "file_size",
    "content_hash",
    "status",
    "auto_index",
    "parse_engine",
    "chunk_size",
    "chunk_overlap",
    "progress",
    "error_message",
    "document_id",
    "chunk_count",
}
_TASK_FIELDS = {
    "status",
    "engine",
    "total_files",
    "processed_files",
    "failed_files",
    "error_message",
    "completed_at",
}
_TERMINAL_FILE_STATUSES = ("ready", "failed", "rejected", "duplicate", "deleted")
_NON_DEDUP_FILE_STATUSES = ("failed", "rejected", "deleted")
_TERMINAL_TASK_STATUSES = ("completed", "partial", "failed")


class UploadStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    # ---- tasks ----
    def create_task(
        self,
        task_id: str,
        engine: str = "auto",
        total_files: int = 0,
        status: str = "queued",
    ) -> dict:
        with Session(self.engine) as session:
            record = UploadTask(
                task_id=task_id,
                status=status,
                engine=engine,
                total_files=total_files,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return self._task_dict(record)

    def get_task(self, task_id: str) -> dict | None:
        with Session(self.engine) as session:
            record = session.execute(
                select(UploadTask).where(UploadTask.task_id == task_id)
            ).scalar_one_or_none()
            return self._task_dict(record) if record else None

    def list_tasks(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with Session(self.engine) as session:
            records = session.execute(
                select(UploadTask)
                .order_by(UploadTask.created_at.desc(), UploadTask.id.desc())
                .limit(limit)
                .offset(offset)
            ).scalars().all()
            return [self._task_dict(r) for r in records]

    def update_task(self, task_id: str, **fields) -> dict | None:
        values = {k: v for k, v in fields.items() if k in _TASK_FIELDS}
        with Session(self.engine) as session:
            record = session.execute(
                select(UploadTask).where(UploadTask.task_id == task_id)
            ).scalar_one_or_none()
            if not record:
                return None
            for key, value in values.items():
                setattr(record, key, value)
            if record.status in _TERMINAL_TASK_STATUSES and record.completed_at is None:
                record.completed_at = datetime.now()
            session.commit()
            session.refresh(record)
            return self._task_dict(record)

    # ---- files ----
    def create_file(
        self,
        upload_id: str,
        original_filename: str,
        stored_path: str,
        doc_format: str,
        file_size: int,
        content_hash: str,
        *,
        task_id: str | None = None,
        auto_index: bool = True,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        status: str = "queued",
    ) -> dict:
        with Session(self.engine) as session:
            record = UploadFile(
                upload_id=upload_id,
                task_id=task_id,
                original_filename=original_filename,
                stored_path=stored_path,
                doc_format=doc_format,
                file_size=file_size,
                content_hash=content_hash,
                status=status,
                auto_index=auto_index,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return self._file_dict(record)

    def get_file(self, upload_id: str) -> dict | None:
        with Session(self.engine) as session:
            record = session.execute(
                select(UploadFile).where(UploadFile.upload_id == upload_id)
            ).scalar_one_or_none()
            return self._file_dict(record) if record else None

    def find_by_hash(
        self,
        content_hash: str,
        *,
        exclude_statuses: tuple[str, ...] = _NON_DEDUP_FILE_STATUSES,
    ) -> dict | None:
        """查找同内容原文件；失败/拒绝记录不应阻止用户重试。"""
        with Session(self.engine) as session:
            stmt = (
                select(UploadFile)
                .where(UploadFile.content_hash == content_hash)
                .order_by(UploadFile.id.desc())
            )
            if exclude_statuses:
                stmt = stmt.where(UploadFile.status.not_in(exclude_statuses))
            record = session.execute(stmt).scalars().first()
            return self._file_dict(record) if record else None

    def list_files(
        self,
        *,
        task_id: str | None = None,
        status: str | None = None,
        doc_format: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        filters = []
        if task_id:
            filters.append(UploadFile.task_id == task_id)
        if status:
            filters.append(UploadFile.status == status)
        if doc_format:
            filters.append(UploadFile.doc_format == doc_format)
        if query:
            like = f"%{query.strip()}%"
            filters.append(
                or_(
                    UploadFile.original_filename.like(like),
                    UploadFile.upload_id.like(like),
                )
            )
        with Session(self.engine) as session:
            count_stmt = select(func.count(UploadFile.id))
            list_stmt = select(UploadFile)
            for condition in filters:
                count_stmt = count_stmt.where(condition)
                list_stmt = list_stmt.where(condition)
            total = int(session.execute(count_stmt).scalar_one())
            records = session.execute(
                list_stmt.order_by(UploadFile.id.desc()).limit(limit).offset(offset)
            ).scalars().all()
            return [self._file_dict(r) for r in records], total

    def files_for_task(self, task_id: str) -> list[dict]:
        with Session(self.engine) as session:
            records = session.execute(
                select(UploadFile)
                .where(UploadFile.task_id == task_id)
                .order_by(UploadFile.id)
            ).scalars().all()
            return [self._file_dict(r) for r in records]

    def list_files_by_statuses(
        self, statuses: tuple[str, ...], *, limit: int = 500
    ) -> list[dict]:
        with Session(self.engine) as session:
            records = session.execute(
                select(UploadFile)
                .where(UploadFile.status.in_(statuses))
                .order_by(UploadFile.id)
                .limit(limit)
            ).scalars().all()
            return [self._file_dict(r) for r in records]

    def stale_files(
        self,
        statuses: tuple[str, ...],
        *,
        minutes: int = 30,
        limit: int = 500,
    ) -> list[dict]:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        with Session(self.engine) as session:
            records = session.execute(
                select(UploadFile)
                .where(UploadFile.status.in_(statuses))
                .where(UploadFile.updated_at < cutoff)
                .order_by(UploadFile.id)
                .limit(limit)
            ).scalars().all()
            return [self._file_dict(r) for r in records]

    def update_file(self, upload_id: str, **fields) -> dict | None:
        values = {k: v for k, v in fields.items() if k in _FILE_FIELDS}
        with Session(self.engine) as session:
            record = session.execute(
                select(UploadFile).where(UploadFile.upload_id == upload_id)
            ).scalar_one_or_none()
            if not record:
                return None
            for key, value in values.items():
                setattr(record, key, value)
            session.commit()
            session.refresh(record)
            return self._file_dict(record)

    def refresh_task_counts(self, task_id: str) -> dict | None:
        """根据文件终态重算批次进度；运行中保持 running。"""
        files = self.files_for_task(task_id)
        if not files:
            return self.update_task(task_id, status="failed", error_message="批次无文件")
        success = sum(1 for f in files if f["status"] in ("indexed", "parsed"))
        failed = sum(1 for f in files if f["status"] in ("failed_parse", "failed_index"))
        processed = success + failed
        if processed < len(files):
            status = "running"
        elif failed == 0:
            status = "completed"
        elif success == 0:
            status = "failed"
        else:
            status = "partial"
        return self.update_task(
            task_id,
            status=status,
            processed_files=processed,
            failed_files=failed,
            error_message=(
                f"{failed} 个文件处理失败" if failed and status in ("partial", "failed") else None
            ),
        )

    def delete_file(self, upload_id: str) -> dict | None:
        with Session(self.engine) as session:
            record = session.execute(
                select(UploadFile).where(UploadFile.upload_id == upload_id)
            ).scalar_one_or_none()
            if not record:
                return None
            result = self._file_dict(record)
            session.execute(delete(UploadAsset).where(UploadAsset.upload_id == upload_id))
            session.delete(record)
            session.commit()
            return result

    # ---- assets ----
    def replace_assets(self, upload_id: str, assets: list[dict]) -> None:
        with Session(self.engine) as session:
            session.execute(delete(UploadAsset).where(UploadAsset.upload_id == upload_id))
            for asset in assets:
                session.add(
                    UploadAsset(
                        upload_id=upload_id,
                        asset_type=asset.get("asset_type") or asset.get("kind") or "image",
                        name=asset["name"],
                        rel_path=asset["rel_path"],
                        page_no=asset.get("page_no"),
                        caption=asset.get("caption") or "",
                        ocr_text=asset.get("ocr_text") or "",
                    )
                )
            session.commit()

    def list_assets(self, upload_id: str) -> list[dict]:
        with Session(self.engine) as session:
            records = session.execute(
                select(UploadAsset)
                .where(UploadAsset.upload_id == upload_id)
                .order_by(UploadAsset.id)
            ).scalars().all()
            return [self._asset_dict(r) for r in records]

    def delete_assets(self, upload_id: str) -> int:
        with Session(self.engine) as session:
            result = session.execute(
                delete(UploadAsset).where(UploadAsset.upload_id == upload_id)
            )
            session.commit()
            return int(result.rowcount or 0)

    # ---- serialization ----
    @staticmethod
    def _task_dict(record: UploadTask) -> dict:
        return {
            "task_id": record.task_id,
            "status": record.status,
            "engine": record.engine,
            "total_files": record.total_files,
            "processed_files": record.processed_files,
            "failed_files": record.failed_files,
            "error_message": record.error_message,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _file_dict(record: UploadFile) -> dict:
        return {
            "upload_id": record.upload_id,
            "task_id": record.task_id,
            "original_filename": record.original_filename,
            "stored_path": record.stored_path,
            "markdown_path": record.markdown_path,
            "doc_format": record.doc_format,
            "file_size": record.file_size,
            "content_hash": record.content_hash,
            "status": record.status,
            "auto_index": bool(record.auto_index),
            "parse_engine": record.parse_engine,
            "chunk_size": record.chunk_size,
            "chunk_overlap": record.chunk_overlap,
            "progress": record.progress,
            "error_message": record.error_message,
            "document_id": record.document_id,
            "chunk_count": record.chunk_count,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    @staticmethod
    def _asset_dict(record: UploadAsset) -> dict:
        return {
            "id": record.id,
            "upload_id": record.upload_id,
            "asset_type": record.asset_type,
            "name": record.name,
            "rel_path": record.rel_path,
            "page_no": record.page_no,
            "caption": record.caption or "",
            "ocr_text": record.ocr_text or "",
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }
