"""阶段 1 测试：知识源注册表（manage_sources.py / knowledge_sources 表）。

全部针对 rag_meta_test 测试库运行，不污染生产 rag_meta。
"""
from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from scripts.manage_sources import KnowledgeSourceRegistry, build_default_sources


def _registry() -> KnowledgeSourceRegistry:
    settings = get_settings()
    admin = sa.create_engine(settings.mysql_url.rsplit("/", 1)[0] + "/", pool_pre_ping=True)
    with admin.begin() as conn:
        conn.execute(
            sa.text("CREATE DATABASE IF NOT EXISTS rag_meta_test CHARACTER SET utf8mb4")
        )
    return KnowledgeSourceRegistry(settings.mysql_url.replace("/rag_meta?", "/rag_meta_test?"))


def test_add_list_remove_roundtrip():
    reg = _registry()
    assert reg.add("测试源", "test-src", "example.com", "A") == "added"
    # 幂等：重复 add 报 exists
    assert reg.add("测试源", "test-src", "example.com", "A") == "exists"

    rows = {r["source_id"]: r for r in reg.list_sources()}
    assert rows["test-src"]["name"] == "测试源"
    assert rows["test-src"]["authority"] == "A"

    assert reg.remove("test-src") is True
    assert reg.remove("test-src") is False
    assert "test-src" not in {r["source_id"] for r in reg.list_sources()}


def test_domains_of_multi_domain():
    reg = _registry()
    reg.add("Go 官方文档", "go-official-test", "go.dev,go-zh.org", "A")
    domains = reg.domains_of("go-official-test")
    assert domains == ["go.dev", "go-zh.org"]
    assert reg.domains_of("not-exist") == []
    reg.remove("go-official-test")


def test_build_default_sources_covers_four_languages():
    sources = build_default_sources()
    assert len(sources) == 9
    ids = {s["source_id"] for s in sources}
    assert {"java-official", "javaguide", "python-official", "liaoxuefeng-py",
            "cppreference-zh", "learncpp", "go-official", "roadmap-sh", "runoob"} == ids
    for s in sources:
        assert s["authority"] in ("A", "B")
        assert s["domains"]
