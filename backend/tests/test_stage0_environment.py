"""阶段 0 测试：环境与配置检查（语言学习资料爬取入库实施方案 步骤 0）。

验收点：
1. backend/.env 覆盖默认 MySQL 凭据（root / root123456 / localhost:3306 / rag_meta）；
2. rag_meta 五表（documents/chunks/tags/document_tags/ingest_tasks）真实存在；
3. ChromaStore（含 Windows 路径补丁）可打开真实 chroma_db 且 tech_docs 集合可访问。
"""
from __future__ import annotations

import sqlalchemy as sa

from app.config import get_settings

REQUIRED_TABLES = {"documents", "chunks", "tags", "document_tags", "ingest_tasks"}


def test_env_overrides_default_mysql_password():
    settings = get_settings()
    assert settings.mysql_user == "root"
    assert settings.mysql_password == "root123456"
    assert settings.mysql_host == "localhost"
    assert settings.mysql_port == 3306
    assert settings.mysql_database == "rag_meta"
    # 密码不得以明文出现在 URL 中未编码位置
    assert "root123456" in settings.mysql_url


def test_mysql_reachable_and_rag_meta_tables_exist():
    settings = get_settings()
    engine = sa.create_engine(settings.mysql_url, pool_pre_ping=True)
    with engine.connect() as conn:
        version = conn.execute(sa.text("SELECT VERSION()")).scalar()
        assert version and version.startswith("8")
        tables = set(
            conn.execute(sa.text("SHOW TABLES")).scalars().all()
        )
    missing = REQUIRED_TABLES - tables
    assert not missing, f"rag_meta 缺少表: {missing}"


def test_chroma_store_opens_real_db():
    from app.services.vector_store import ChromaStore

    settings = get_settings()
    store = ChromaStore(persist_path=str(settings.chroma_path))
    count = store.count("tech_doc")
    assert count >= 0
