"""知识源注册表 CLI（语言学习资料爬取入库实施方案 步骤 1）。

爬取前把知识源登记到 rag_meta.knowledge_sources（authority A=官方文档 / B=优质社区教程），
供种子清单校验与后续溯源使用。

用法:
  python scripts/manage_sources.py add --name "Java 官方教程" --source-id java-official \
      --domains docs.oracle.com --authority A
  python scripts/manage_sources.py list
  python scripts/manage_sources.py remove --source-id java-official
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from app.config import get_settings

KNOWLEDGE_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_id  VARCHAR(64)  NOT NULL UNIQUE,
    name       VARCHAR(128) NOT NULL,
    domains    VARCHAR(512) NOT NULL,
    authority  ENUM('A', 'B') NOT NULL DEFAULT 'B',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def ensure_table(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(KNOWLEDGE_SOURCES_DDL))


class KnowledgeSourceRegistry:
    """knowledge_sources 表的增删查（SQLAlchemy 同步实现）。"""

    def __init__(self, url: str):
        self.engine = sa.create_engine(url, pool_pre_ping=True)
        ensure_table(self.engine)

    def add(self, name: str, source_id: str, domains: str, authority: str) -> str:
        """登记来源；source_id 已存在时返回 'exists'，否则 'added'。"""
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT 1 FROM knowledge_sources WHERE source_id = :sid"),
                {"sid": source_id},
            ).first()
            if row:
                return "exists"
            conn.execute(
                sa.text(
                    "INSERT INTO knowledge_sources (source_id, name, domains, authority) "
                    "VALUES (:sid, :name, :domains, :authority)"
                ),
                {"sid": source_id, "name": name, "domains": domains, "authority": authority},
            )
        return "added"

    def list_sources(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT source_id, name, domains, authority, created_at "
                    "FROM knowledge_sources ORDER BY authority, source_id"
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    def remove(self, source_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                sa.text("DELETE FROM knowledge_sources WHERE source_id = :sid"),
                {"sid": source_id},
            )
        return result.rowcount > 0

    def domains_of(self, source_id: str) -> list[str]:
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT domains FROM knowledge_sources WHERE source_id = :sid"),
                {"sid": source_id},
            ).first()
        if not row:
            return []
        return [d.strip().lower() for d in str(row[0]).split(",") if d.strip()]


def build_default_sources() -> list[dict]:
    """方案步骤 1 规定的 9 个知识源。"""
    return [
        {"name": "Java 官方教程", "source_id": "java-official", "domains": "docs.oracle.com", "authority": "A"},
        {"name": "JavaGuide", "source_id": "javaguide", "domains": "javaguide.cn", "authority": "B"},
        {"name": "Python 官方教程(中文)", "source_id": "python-official", "domains": "docs.python.org", "authority": "A"},
        {"name": "廖雪峰Python教程", "source_id": "liaoxuefeng-py", "domains": "liaoxuefeng.com", "authority": "B"},
        {"name": "cppreference(中文)", "source_id": "cppreference-zh", "domains": "zh.cppreference.com", "authority": "A"},
        {"name": "LearnCpp", "source_id": "learncpp", "domains": "learncpp.com", "authority": "B"},
        {"name": "Go 官方文档", "source_id": "go-official", "domains": "go.dev,go-zh.org", "authority": "A"},
        {"name": "roadmap.sh 学习路线", "source_id": "roadmap-sh", "domains": "roadmap.sh", "authority": "B"},
        {"name": "菜鸟教程", "source_id": "runoob", "domains": "runoob.com", "authority": "B"},
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识源注册表管理")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="登记知识源")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--source-id", required=True)
    p_add.add_argument("--domains", required=True, help="逗号分隔的域名列表")
    p_add.add_argument("--authority", choices=["A", "B"], default="B")

    p_list = sub.add_parser("list", help="列出知识源")
    p_remove = sub.add_parser("remove", help="移除知识源")
    p_remove.add_argument("--source-id", required=True)

    args = parser.parse_args(argv)
    registry = KnowledgeSourceRegistry(get_settings().mysql_url)

    if args.command == "add":
        status = registry.add(args.name, args.source_id, args.domains, args.authority)
        print(f"[{status}] {args.source_id}")
        return 0
    if args.command == "list":
        for row in registry.list_sources():
            print(f"[{row['authority']}] {row['source_id']:<20} {row['name']:<24} {row['domains']}")
        print(f"共 {len(registry.list_sources())} 个来源")
        return 0
    if args.command == "remove":
        print("removed" if registry.remove(args.source_id) else "not-found")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
