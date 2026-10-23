"""上传文件与解析资源的本地存储（PRD 7.9 / P4）。

所有路径都先 ``resolve`` 再校验必须位于上传根目录内；原文件分块写入并同步
计算 SHA-256，超过上限立即删除半成品。解析资源由解析器写入 ``assets/``，
本模块只负责边界、原子写入与清理。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings

_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


class UploadStorageError(RuntimeError):
    pass


class FileTooLargeError(UploadStorageError):
    pass


class EmptyFileError(UploadStorageError):
    pass


class UploadAssetStore:
    def __init__(self, root: Path | str | None = None, max_bytes: int | None = None):
        settings = get_settings()
        self.root = Path(root or settings.upload_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(max_bytes if max_bytes is not None else settings.upload_max_mb * 1024 * 1024)

    def _validate_id(self, upload_id: str) -> str:
        upload_id = str(upload_id or "")
        if not _UPLOAD_ID_RE.match(upload_id):
            raise UploadStorageError(f"非法 upload_id: {upload_id!r}")
        return upload_id

    def upload_dir(self, upload_id: str) -> Path:
        target = (self.root / self._validate_id(upload_id)).resolve()
        if target != self.root and self.root not in target.parents:
            raise UploadStorageError("上传目录越界")
        return target

    def create_upload(self, upload_id: str) -> Path:
        target = self.upload_dir(upload_id)
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def safe_filename(filename: str) -> str:
        raw = Path(str(filename or "")).name.strip()
        raw = _INVALID_FILENAME_RE.sub("_", raw).strip(" .")
        if not raw or raw in (".", ".."):
            raw = "upload.bin"
        if len(raw) > 240:
            suffix = Path(raw).suffix
            raw = raw[: 240 - len(suffix)] + suffix
        return raw

    def save_stream(
        self,
        upload_id: str,
        filename: str,
        stream: BinaryIO,
        *,
        max_bytes: int | None = None,
    ) -> dict:
        """分块写入原文件，返回 size/sha256/安全文件名/绝对路径。"""
        limit = int(max_bytes if max_bytes is not None else self.max_bytes)
        upload_dir = self.create_upload(upload_id)
        safe_name = self.safe_filename(filename)
        target = (upload_dir / safe_name).resolve()
        if upload_dir not in target.parents:
            raise UploadStorageError("原文件路径越界")
        temp = upload_dir / f".{safe_name}.part"
        digest = hashlib.sha256()
        total = 0
        try:
            with temp.open("wb") as handle:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise FileTooLargeError(
                            f"文件超过 {limit // (1024 * 1024)} MB"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if total == 0:
                raise EmptyFileError("文件为空")
            os.replace(temp, target)
        except Exception:
            temp.unlink(missing_ok=True)
            # 空目录也清理，避免拒绝/失败留下半成品。
            self._remove_dir_if_empty(upload_dir)
            raise
        return {
            "original_filename": str(filename or safe_name),
            "safe_filename": safe_name,
            "stored_path": str(target),
            "file_size": total,
            "content_hash": digest.hexdigest(),
        }

    def write_markdown(self, upload_id: str, markdown: str) -> Path:
        upload_dir = self.create_upload(upload_id)
        target = (upload_dir / "parsed.md").resolve()
        if upload_dir not in target.parents:
            raise UploadStorageError("Markdown 路径越界")
        temp = upload_dir / ".parsed.md.part"
        temp.write_text(markdown, encoding="utf-8")
        os.replace(temp, target)
        return target

    def markdown_path(self, upload_id: str) -> Path:
        path = (self.upload_dir(upload_id) / "parsed.md").resolve()
        if self.upload_dir(upload_id) not in path.parents:
            raise UploadStorageError("Markdown 路径越界")
        return path

    def original_path(self, upload_id: str, stored_path: str | None = None) -> Path:
        if stored_path:
            path = Path(stored_path).resolve()
            upload_dir = self.upload_dir(upload_id)
            if path != upload_dir and upload_dir not in path.parents:
                raise UploadStorageError("原文件路径越界")
            return path
        upload_dir = self.upload_dir(upload_id)
        candidates = [p for p in upload_dir.iterdir() if p.is_file() and p.name != "parsed.md"]
        if not candidates:
            raise UploadStorageError("原文件不存在")
        return candidates[0].resolve()

    def asset_path(self, upload_id: str, name: str) -> Path:
        raw = str(name or "").replace("\\", "/").lstrip("/")
        if not raw or ".." in Path(raw).parts:
            raise UploadStorageError("资源路径非法")
        assets_dir = (self.upload_dir(upload_id) / "assets").resolve()
        target = (assets_dir / raw).resolve()
        if target != assets_dir and assets_dir not in target.parents:
            raise UploadStorageError("资源路径越界")
        return target

    def clear_parsed(self, upload_id: str) -> None:
        upload_dir = self.upload_dir(upload_id)
        (upload_dir / "parsed.md").unlink(missing_ok=True)
        assets = upload_dir / "assets"
        if assets.exists():
            shutil.rmtree(assets, ignore_errors=True)

    def delete_upload(self, upload_id: str) -> bool:
        target = self.upload_dir(upload_id)
        if not target.exists():
            return False
        shutil.rmtree(target, ignore_errors=False)
        return True

    def _remove_dir_if_empty(self, path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            pass

    @staticmethod
    def is_image(name: str) -> bool:
        return Path(str(name)).suffix.lower() in _IMAGE_SUFFIXES
