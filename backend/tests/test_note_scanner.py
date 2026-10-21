"""阶段 1 测试：本地 Markdown 笔记扫描（PRD 3.1.2）。"""
from pathlib import Path

from app.config import BACKEND_DIR
from app.services.note_scanner import (
    normalize_file_path,
    parse_front_matter,
    scan_markdown_files,
)

NOTE_WITH_FM = """---
title: ChromaDB 入门
tags: [chroma, vector-db]
created_at: 2026-01-01
updated_at: 2026-06-01
---

# ChromaDB 入门

正文内容在这里。
"""

NOTE_PLAIN = """# 纯笔记

没有 front matter。
"""


def test_parse_front_matter(tmp_path):
    meta, body = parse_front_matter(NOTE_WITH_FM)
    assert meta["title"] == "ChromaDB 入门"
    assert meta["tags"] == ["chroma", "vector-db"]
    assert body.startswith("# ChromaDB 入门")


def test_parse_front_matter_missing():
    meta, body = parse_front_matter(NOTE_PLAIN)
    assert meta == {}
    assert body == NOTE_PLAIN


def test_scan_markdown_files(tmp_path):
    (tmp_path / "a.md").write_text(NOTE_WITH_FM, encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.md").write_text(NOTE_PLAIN, encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("not markdown", encoding="utf-8")

    files = scan_markdown_files(tmp_path)
    assert len(files) == 2

    a = next(f for f in files if f["file_path"].endswith("a.md"))
    assert a["title"] == "ChromaDB 入门"
    assert a["tags"] == ["chroma", "vector-db"]
    assert a["source_type"] == "personal_note"
    assert a["created_at"] == "2026-01-01"
    assert a["content"].startswith("# ChromaDB 入门")

    b = next(f for f in files if f["file_path"].endswith("b.md"))
    assert b["title"] == "b"  # 无 front matter 时用文件名
    assert b["tags"] == []


def test_scan_nonexistent_dir(tmp_path):
    assert scan_markdown_files(tmp_path / "nope") == []


def test_scan_single_tag_string(tmp_path):
    (tmp_path / "c.md").write_text(
        "---\ntitle: T\ntags: solo\n---\n\nbody", encoding="utf-8"
    )
    files = scan_markdown_files(tmp_path)
    assert files[0]["tags"] == ["solo"]


def test_scan_source_url_from_front_matter(tmp_path):
    """爬虫落盘的 md 带 source_url front-matter，扫描器必须读出来。"""
    fm = "---\ntitle: FastAPI 入门\nsource_url: https://fastapi.tiangolo.com/tutorial/\nsource_type: tech_doc\n---\n\n# body"
    (tmp_path / "crawl.md").write_text(fm, encoding="utf-8")
    files = scan_markdown_files(tmp_path, source_type="tech_doc")
    assert len(files) == 1
    assert files[0]["source_url"] == "https://fastapi.tiangolo.com/tutorial/"
    assert files[0]["title"] == "FastAPI 入门"


def test_scan_source_url_default_empty(tmp_path):
    """没有 source_url front-matter 时返回空串（兜底）。"""
    (tmp_path / "plain.md").write_text("# 纯笔记", encoding="utf-8")
    files = scan_markdown_files(tmp_path)
    assert files[0]["source_url"] == ""


def test_normalize_file_path_under_backend_is_relative_posix():
    """backend 目录内路径统一为 data/... 形式，避免绝对/相对路径重复入库。"""
    absolute = BACKEND_DIR / "data" / "notes" / "chroma.md"
    assert normalize_file_path(absolute) == "data/notes/chroma.md"
    assert normalize_file_path(str(absolute)) == "data/notes/chroma.md"


def test_normalize_file_path_outside_backend_uses_posix():
    outside = Path("C:/temp/notes/other.md")
    assert normalize_file_path(outside) == "C:/temp/notes/other.md"
