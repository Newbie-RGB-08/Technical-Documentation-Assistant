"""受限 ZIP 解压（PRD P2 / MinerU 结果安全处理）。

约束：最多 1000 个条目、解压总量最多 500 MB；拒绝绝对路径、``..``
穿越与符号链接。所有文件最终必须位于目标目录内。
"""
from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_ENTRIES = 1000
MAX_TOTAL_BYTES = 500 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


class UnsafeArchiveError(ValueError):
    pass


def _normalized_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or normalized.startswith("/"):
        raise UnsafeArchiveError(f"拒绝 ZIP 绝对路径: {name!r}")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise UnsafeArchiveError(f"拒绝 ZIP 盘符路径: {name!r}")
    parts = tuple(part for part in pure.parts if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise UnsafeArchiveError(f"拒绝 ZIP 路径穿越: {name!r}")
    return parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def safe_extract_zip(
    zip_path: Path,
    out_dir: Path,
    *,
    max_entries: int = MAX_ENTRIES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> list[Path]:
    """安全解压，返回实际写出的文件路径列表。"""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise UnsafeArchiveError(f"ZIP 条目数 {len(infos)} 超过上限 {max_entries}")
        declared_total = sum(max(0, info.file_size) for info in infos if not info.is_dir())
        if declared_total > max_total_bytes:
            raise UnsafeArchiveError(
                f"ZIP 解压总量 {declared_total} 超过上限 {max_total_bytes}"
            )
        total_written = 0
        for info in infos:
            if _is_symlink(info):
                raise UnsafeArchiveError(f"拒绝 ZIP 符号链接: {info.filename!r}")
            parts = _normalized_parts(info.filename)
            if not parts:
                continue
            target = out_dir.joinpath(*parts).resolve()
            if target != out_dir and out_dir not in target.parents:
                raise UnsafeArchiveError(f"ZIP 条目越界: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as dest:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if total_written > max_total_bytes:
                        raise UnsafeArchiveError(
                            f"ZIP 实际解压总量超过上限 {max_total_bytes}"
                        )
                    dest.write(chunk)
            extracted.append(target)
    return extracted


def collect_images(files: list[Path], out_dir: Path) -> list[tuple[Path, str]]:
    """把解压结果中的图片统一收集到 out_dir/assets，返回 (路径, 相对路径)。

    非图片文件保持原位；重名图片自动追加序号，避免覆盖。
    """
    out_dir = Path(out_dir).resolve()
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    result: list[tuple[Path, str]] = []
    used: set[str] = set()
    for source in files:
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        name = source.name
        stem, suffix = source.stem, source.suffix
        index = 1
        while name in used:
            name = f"{stem}_{index}{suffix}"
            index += 1
        used.add(name)
        target = assets_dir / name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        result.append((target, f"assets/{name}"))
    return result
