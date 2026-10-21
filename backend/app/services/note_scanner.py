"""本地 Markdown 笔记扫描（PRD 3.1.2）。

递归扫描目录收集 .md，解析 YAML front-matter（title/tags/created_at/updated_at），
组装文件级元数据。爬取的文档复用同一结构（source_type=tech_doc）。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from app.config import BACKEND_DIR

FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def normalize_file_path(path: str | Path) -> str:
    """入库路径统一为相对 backend 的 POSIX 形式。

    同一份笔记可能被以绝对路径和相对路径各入库一次，统一后可以从源头避免重复。
    """
    p = Path(path)
    try:
        return p.resolve().relative_to(BACKEND_DIR).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def parse_front_matter(text: str) -> tuple[dict, str]:
    """解析 front-matter，返回 (元数据字典, 正文)。"""
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        if value != "":
            meta[key] = value
    return meta, text[m.end():]


def _file_meta_from_path(p: Path, source_type: str) -> dict:
    stat = p.stat()
    return {
        "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(
            timespec="seconds"
        ),
        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(
            timespec="seconds"
        ),
        "source_type": source_type,
    }


def scan_markdown_files(directory: str | Path, source_type: str = "personal_note") -> list[dict]:
    """扫描目录下全部 .md，返回文件级元数据列表（不含分块）。

    每项: {file_path, source_type, title, tags, created_at, updated_at, source_url, content}
    """
    root = Path(directory)
    if not root.exists():
        return []

    files: list[dict] = []
    for p in sorted(root.rglob("*.md")):
        if not p.is_file():
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_front_matter(raw)
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        meta = _file_meta_from_path(p, source_type)
        files.append(
            {
                "file_path": normalize_file_path(p),
                "source_type": source_type,
                "title": str(fm.get("title") or p.stem),
                "tags": tags,
                "created_at": str(fm.get("created_at") or meta["created_at"]),
                "updated_at": str(fm.get("updated_at") or meta["updated_at"]),
                "source_url": str(fm.get("source_url") or ""),
                "canonical_url": str(fm.get("canonical_url") or ""),
                "origin": str(fm.get("origin") or ""),
                "retrieved_at": str(fm.get("retrieved_at") or ""),
                "expires_at": str(fm.get("expires_at") or ""),
                "search_query": str(fm.get("search_query") or ""),
                "provider": str(fm.get("provider") or ""),
                "content": body,
            }
        )
    return files
