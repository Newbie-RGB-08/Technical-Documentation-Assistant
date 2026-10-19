"""阶段 1 测试：本地 Markdown 笔记扫描（PRD 3.1.2）。"""
from app.services.note_scanner import parse_front_matter, scan_markdown_files

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
