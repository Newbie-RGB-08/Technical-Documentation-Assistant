"""MySQL 元数据存储实现（PRD 3.2.3 / 3.2.4）。

表：documents / chunks / tags / document_tags / ingest_tasks
引擎：SQLAlchemy + PyMySQL（同步，个人工具数据量小，可靠优先）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.config import get_settings
from app.services.base_stores import MetadataStore


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(512), unique=True)
    source_type: Mapped[str] = mapped_column(Enum("tech_doc", "personal_note"))
    title: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_ingested: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        Index("idx_source_type", "source_type"),
        Index("idx_updated_at", "updated_at"),
        Index("idx_content_hash", "content_hash"),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    chroma_id: Mapped[str] = mapped_column(String(255), unique=True)
    heading_path: Mapped[str | None] = mapped_column(String(512))
    token_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (Index("idx_document", "document_id"),)


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DocumentTag(Base):
    __tablename__ = "document_tags"
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class IngestTask(Base):
    __tablename__ = "ingest_tasks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "processing", "completed", "failed"), default="pending"
    )
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MySQLMetadataStore(MetadataStore):
    def __init__(self, url: str | None = None, create_all: bool = True):
        settings = get_settings()
        self.engine = create_engine(
            url or settings.mysql_url, pool_pre_ping=True, pool_recycle=1800
        )
        if create_all:
            Base.metadata.create_all(self.engine)

    # ---- documents ----
    def upsert_document(self, doc: dict) -> int:
        with Session(self.engine) as session:
            existing = session.execute(
                select(Document).where(Document.file_path == doc["file_path"])
            ).scalar_one_or_none()
            if existing:
                for field in ("source_type", "title", "content_hash", "chunk_count"):
                    if field in doc:
                        setattr(existing, field, doc[field])
                existing.last_ingested = datetime.now()
                doc_id = existing.id
            else:
                record = Document(
                    file_path=doc["file_path"],
                    source_type=doc["source_type"],
                    title=doc.get("title"),
                    content_hash=doc["content_hash"],
                    chunk_count=doc.get("chunk_count", 0),
                    last_ingested=datetime.now(),
                )
                session.add(record)
                session.flush()
                doc_id = record.id
            session.commit()
            return doc_id

    def get_document(self, file_path: str) -> dict | None:
        with Session(self.engine) as session:
            record = session.execute(
                select(Document).where(Document.file_path == file_path)
            ).scalar_one_or_none()
            return self._doc_dict(record) if record else None

    def list_documents(self, source_type: str | None = None) -> list[dict]:
        with Session(self.engine) as session:
            stmt = select(Document).order_by(Document.id)
            if source_type:
                stmt = stmt.where(Document.source_type == source_type)
            records = session.execute(stmt).scalars().all()
            return [self._doc_dict(r) for r in records]

    def delete_document(self, file_path: str) -> int:
        with Session(self.engine) as session:
            record = session.execute(
                select(Document).where(Document.file_path == file_path)
            ).scalar_one_or_none()
            if not record:
                return -1
            doc_id = record.id
            session.execute(delete(Document).where(Document.id == doc_id))
            session.commit()
            return doc_id

    def save_tags(self, document_id: int, tags: list[str]) -> None:
        with Session(self.engine) as session:
            session.execute(delete(DocumentTag).where(DocumentTag.document_id == document_id))
            for name in tags:
                tag_id = session.execute(
                    select(Tag.id).where(Tag.name == name)
                ).scalar_one_or_none()
                if tag_id is None:
                    tag = Tag(name=name)
                    session.add(tag)
                    session.flush()
                    tag_id = tag.id
                session.add(DocumentTag(document_id=document_id, tag_id=tag_id))
            session.commit()

    # ---- chunks ----
    def save_chunks(
        self, document_id: int, chunks: list[dict], vector_ids: list[str]
    ) -> None:
        with Session(self.engine) as session:
            session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            for chunk, vid in zip(chunks, vector_ids):
                session.add(
                    Chunk(
                        document_id=document_id,
                        chunk_index=chunk["metadata"].get("global_index", 0),
                        chroma_id=vid,
                        heading_path=chunk["metadata"].get("heading_path"),
                        token_count=len(chunk["text"]),
                    )
                )
            session.commit()

    def get_chunks_by_document(self, document_id: int) -> list[dict]:
        with Session(self.engine) as session:
            records = session.execute(
                select(Chunk).where(Chunk.document_id == document_id)
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "document_id": r.document_id,
                    "chunk_index": r.chunk_index,
                    "chroma_id": r.chroma_id,
                    "heading_path": r.heading_path,
                }
                for r in records
            ]

    # ---- ingest tasks ----
    def create_task(self, task_id: str, file_count: int) -> None:
        with Session(self.engine) as session:
            session.add(IngestTask(task_id=task_id, file_count=file_count, status="pending"))
            session.commit()

    def update_task(self, task_id: str, **fields) -> None:
        with Session(self.engine) as session:
            record = session.execute(
                select(IngestTask).where(IngestTask.task_id == task_id)
            ).scalar_one_or_none()
            if not record:
                return
            for key, value in fields.items():
                setattr(record, key, value)
            if fields.get("status") in ("completed", "failed"):
                record.completed_at = datetime.now()
            session.commit()

    def get_task(self, task_id: str) -> dict | None:
        with Session(self.engine) as session:
            record = session.execute(
                select(IngestTask).where(IngestTask.task_id == task_id)
            ).scalar_one_or_none()
            if not record:
                return None
            return {
                "task_id": record.task_id,
                "status": record.status,
                "file_count": record.file_count,
                "chunk_count": record.chunk_count,
                "error_message": record.error_message,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            }

    @staticmethod
    def _doc_dict(r: Document) -> dict:
        return {
            "id": r.id,
            "file_path": r.file_path,
            "source_type": r.source_type,
            "title": r.title,
            "content_hash": r.content_hash,
            "chunk_count": r.chunk_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "last_ingested": r.last_ingested.isoformat() if r.last_ingested else None,
        }


def init_mysql_schema(url: str | None = None) -> None:
    """确保数据库存在并建表（UTF8MB4）。"""
    settings = get_settings()
    target = url or settings.mysql_url
    admin_url = target.rsplit("/", 1)[0] + "/"
    db_name = settings.mysql_database
    tmp_engine = create_engine(admin_url)
    with tmp_engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.commit()
    tmp_engine.dispose()
    MySQLMetadataStore(url=target, create_all=True)
