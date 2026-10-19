"""领域词表构建脚本：从知识库元数据自动生成关键字权重表。

用法: python scripts/build_domain_terms.py
输出: data/domain_terms.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.retrieval_scorer import build_keyword_weights
from app.services.metadata_store import MySQLMetadataStore


def main() -> int:
    settings = get_settings()
    try:
        store = MySQLMetadataStore(create_all=True)
        docs = store.list_documents()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] MySQL 不可用（{exc}），使用空词表")
        docs = []

    tags = []
    titles = []
    heading_paths = []
    for doc in docs:
        titles.append(doc.get("title") or "")
    with store.engine.connect() as conn:
        from sqlalchemy import text

        tag_rows = conn.execute(text("SELECT DISTINCT name FROM tags")).fetchall()
        tags = [r[0] for r in tag_rows]
        hp_rows = conn.execute(
            text("SELECT DISTINCT heading_path FROM chunks WHERE heading_path IS NOT NULL")
        ).fetchall()
        heading_paths = [r[0] for r in hp_rows]

    weights = build_keyword_weights(tags=tags, titles=titles, heading_paths=heading_paths)
    out_path = settings.data_dir / "domain_terms.json"
    out_path.write_text(
        json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] 领域词表已生成: {out_path}（{len(weights)} 个词条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
