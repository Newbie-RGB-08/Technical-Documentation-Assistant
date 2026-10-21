"""清理过期网页知识（PRD E.10）。

按 front-matter 的 expires_at 删除已过期网页文档（MySQL 元数据 + Chroma 向量 + 本地文件），
并清理 SQLite 搜索/抓取缓存。

用法:
    python scripts/prune_web_cache.py            # 只清理已过期网页知识
    python scripts/prune_web_cache.py --days 30  # 额外清理 30 天前的网页知识
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.services.metadata_store import MySQLMetadataStore
from app.services.note_scanner import parse_front_matter
from app.services.vector_store import ChromaStore
from app.services.web_cache_store import WebCacheStore


def _parse_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理过期网页知识")
    parser.add_argument("--days", type=float, default=None, help="额外清理 N 天前的网页知识")
    args = parser.parse_args(argv)

    settings = get_settings()
    web_dir = Path(settings.tech_docs_dir) / "web"
    now = datetime.now().astimezone()
    cutoff = now - timedelta(days=float(args.days)) if args.days else None

    metadata = MySQLMetadataStore(create_all=True)
    chroma = ChromaStore()
    cache = WebCacheStore()

    removed = 0
    if web_dir.exists():
        for path in sorted(web_dir.rglob("*.md")):
            try:
                fm, _body = parse_front_matter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                print(f"[WARN] 读取失败 {path}: {exc}")
                continue
            if str(fm.get("origin") or "") != "web_search":
                continue
            expires_at = _parse_time(fm.get("expires_at"))
            retrieved_at = _parse_time(fm.get("retrieved_at"))
            expired = expires_at is not None and expires_at <= now
            if not expired and cutoff is not None and retrieved_at is not None:
                expired = retrieved_at <= cutoff
            if not expired:
                continue

            file_path = str(path)
            metadata.delete_document(file_path)
            chroma.delete_by_metadata("tech_doc", {"file_path": file_path})
            try:
                path.unlink()
            except OSError as exc:
                print(f"[WARN] 删除文件失败 {path}: {exc}")
                continue
            removed += 1

    pruned = cache.prune(days=settings.web_cache_ttl_days)
    print(
        f"[OK] 已清理网页知识 {removed} 篇；缓存 search={pruned['search']} "
        f"pages={pruned['pages']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
