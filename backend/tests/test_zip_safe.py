"""P2：ZIP 安全解压边界。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.parsers.zip_safe import UnsafeArchiveError, collect_images, safe_extract_zip


def _zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_safe_zip_extracts_and_collects_images(tmp_path):
    archive = _zip(
        tmp_path,
        {"result/a.md": b"# hi", "result/image.png": b"png-bytes", "result/nested/x.txt": b"x"},
    )
    out = tmp_path / "out"
    files = safe_extract_zip(archive, out)
    assert (out / "result/a.md").read_text(encoding="utf-8") == "# hi"
    assert all(out in f.parents for f in files)
    images = collect_images(files, out)
    assert images and images[0][0] == out / "assets" / "image.png"
    assert images[0][1] == "assets/image.png"


def test_zip_traversal_and_absolute_paths_rejected(tmp_path):
    for name in ("../evil.txt", "/absolute.txt", "C:/drive.txt", "a/../../evil.txt"):
        archive = _zip(tmp_path, {name: b"x"})
        with pytest.raises(UnsafeArchiveError):
            safe_extract_zip(archive, tmp_path / "out")
        archive.unlink()


def test_zip_symlink_rejected(tmp_path):
    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (0o120777 << 16)  # S_IFLNK
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(info, "target")
    with pytest.raises(UnsafeArchiveError, match="符号链接"):
        safe_extract_zip(archive, tmp_path / "out")


def test_zip_entry_and_size_limits(tmp_path):
    archive = _zip(tmp_path, {"a.txt": b"a", "b.txt": b"b", "c.txt": b"c"})
    with pytest.raises(UnsafeArchiveError, match="条目数"):
        safe_extract_zip(archive, tmp_path / "out1", max_entries=2)
    with pytest.raises(UnsafeArchiveError, match="解压总量"):
        safe_extract_zip(archive, tmp_path / "out2", max_total_bytes=1)
