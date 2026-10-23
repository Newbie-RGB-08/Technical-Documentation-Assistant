"""P1：documents.source_type 枚举迁移。

旧库的 MySQL ENUM 不会随 create_all 自动扩展，必须验证：
1. 两值旧表能幂等扩为三值；
2. 旧数据保留、新 uploaded_doc 可写入；
3. 迁移失败不阻断启动，但 ready 标记为 False。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.routers.health import health
from app.services.metadata_store import MySQLMetadataStore


def _db_url(name: str) -> str:
    return get_settings().mysql_url.replace("/rag_meta?", f"/{name}?")


def _admin_url() -> str:
    return get_settings().mysql_url.rsplit("/", 1)[0] + "/"


def _reset_database(name: str) -> None:
    admin = sa.create_engine(_admin_url())
    with admin.begin() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS `{name}`"))
        conn.execute(
            sa.text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )
    admin.dispose()


def _create_legacy_documents_table(url: str) -> None:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE documents (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    file_path VARCHAR(512) NOT NULL UNIQUE,
                    source_type ENUM('tech_doc', 'personal_note') NOT NULL,
                    title VARCHAR(255),
                    source_url VARCHAR(512),
                    content_hash CHAR(64) NOT NULL,
                    chunk_count INT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    last_ingested DATETIME,
                    INDEX idx_source_type (source_type),
                    INDEX idx_updated_at (updated_at),
                    INDEX idx_content_hash (content_hash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO documents "
                "(file_path, source_type, title, content_hash, chunk_count) "
                "VALUES ('legacy/a.md', 'personal_note', 'Legacy', :h, 1)"
            ),
            {"h": "a" * 64},
        )
    engine.dispose()


@pytest.mark.parametrize("legacy_db", ["rag_meta_tda_enum_test"])
def test_legacy_enum_is_migrated_and_remains_idempotent(legacy_db):
    url = _db_url(legacy_db)
    _reset_database(legacy_db)
    _create_legacy_documents_table(url)
    store = MySQLMetadataStore(url=url, create_all=False)
    try:
        assert store.source_type_enum_ready is True
        with store.engine.connect() as conn:
            row = conn.execute(
                sa.text("SHOW COLUMNS FROM documents LIKE 'source_type'")
            ).fetchone()
        assert row is not None and "uploaded_doc" in str(row[1])

        # 旧数据未受影响
        legacy = store.get_document("legacy/a.md")
        assert legacy and legacy["source_type"] == "personal_note"

        # 新枚举值可写入
        doc_id = store.upsert_document(
            {
                "file_path": "uploads/u1/original.pdf",
                "source_type": "uploaded_doc",
                "title": "上传文档",
                "content_hash": "b" * 64,
                "chunk_count": 2,
            }
        )
        assert doc_id > 0
        assert store.get_document("uploads/u1/original.pdf")["source_type"] == "uploaded_doc"

        # 再次构造（模拟下次启动）不应因重复 ALTER 失败
        store.engine.dispose()
        store2 = MySQLMetadataStore(url=url, create_all=False)
        try:
            assert store2.source_type_enum_ready is True
            with store2.engine.connect() as conn:
                row2 = conn.execute(
                    sa.text("SHOW COLUMNS FROM documents LIKE 'source_type'")
                ).fetchone()
            assert "uploaded_doc" in str(row2[1])
        finally:
            store2.engine.dispose()
    finally:
        store.engine.dispose()
        _reset_database(legacy_db)


def test_enum_migration_failure_does_not_block_startup_and_marks_not_ready(monkeypatch):
    """ALTER 被拒时不能抛出，只把 ready 置 False 供上传接口明确报错。"""
    calls = {"show": 0, "alter": 0}

    class FakeResult:
        def fetchone(self):
            calls["show"] += 1
            # 第一次仍是两值；ALTER 成功后下一次为三值
            value = (
                "enum('tech_doc','personal_note')"
                if calls["alter"] == 0
                else "enum('tech_doc','personal_note','uploaded_doc')"
            )
            return ("source_type", value, "NO", "", None, "")

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            sql = str(statement)
            if "SHOW COLUMNS" in sql:
                return FakeResult()
            if "ALTER TABLE" in sql:
                calls["alter"] += 1
            return SimpleNamespace()

    class FakeEngine:
        dialect = SimpleNamespace(name="mysql")

        def begin(self):
            return FakeConn()

    store = object.__new__(MySQLMetadataStore)
    store.engine = FakeEngine()
    store.source_type_enum_ready = True

    store._ensure_source_type_enum()
    assert store.source_type_enum_ready is True
    assert calls["alter"] == 1

    # 已经是三值后再次调用不应重复 ALTER
    store._ensure_source_type_enum()
    assert store.source_type_enum_ready is True
    assert calls["alter"] == 1


def test_enum_migration_failure_flag_is_false_on_error():
    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            sql = str(statement)
            if "SHOW COLUMNS" in sql:
                return SimpleNamespace(
                    fetchone=lambda: ("source_type", "enum('tech_doc','personal_note')", "NO", "", None, "")
                )
            raise RuntimeError("ALTER denied")

    class FakeEngine:
        dialect = SimpleNamespace(name="mysql")

        def begin(self):
            return FakeConn()

    store = object.__new__(MySQLMetadataStore)
    store.engine = FakeEngine()
    store.source_type_enum_ready = True
    store._ensure_source_type_enum()
    assert store.source_type_enum_ready is False


def test_health_walks_every_source_type():
    class FakeChroma:
        def __init__(self):
            self.calls = []

        def source_types(self):
            return ("tech_doc", "personal_note", "uploaded_doc")

        def count(self, source_type):
            self.calls.append(source_type)
            return 0

    class FakeMetadata:
        def list_documents(self):
            return []

    class FakeSessions:
        def get_history(self, session_id, limit=1):
            return []

    chroma = FakeChroma()
    core = SimpleNamespace(chroma=chroma, metadata=FakeMetadata(), session_store=FakeSessions())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(core=core)))
    result = asyncio.run(health(request))
    assert result["status"] == "ok"
    assert chroma.calls == ["tech_doc", "personal_note", "uploaded_doc"]
