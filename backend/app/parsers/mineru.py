"""MinerU HTTP 解析适配器（PRD 7.4.3 / P7-1）。

调用 `POST {uri}/file_parse`（multipart）获取 ZIP，安全解压后优先取
``full.md``，图片收进 ``assets/`` 并改写 Markdown 链接。依赖 httpx 同步
客户端；http 服务不可达时仅该引擎不可用，不影响服务启动/降级。
"""
from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from pathlib import Path

import httpx

from app.config import get_settings
from app.parsers.base import (
    DocumentParser,
    ParsedAsset,
    ParsedDocument,
    ParserError,
    UnsupportedFormatError,
)
from app.parsers.native import IMAGE_EXTENSIONS
from app.parsers.zip_safe import UnsafeArchiveError, collect_images, safe_extract_zip

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 5.0


class MinerUParser(DocumentParser):
    name = "mineru"
    supported_extensions = (".pdf",) + IMAGE_EXTENSIONS

    def available(self) -> bool:
        """依赖 httpx 即可直接尝试；真实可用性交由 health() 探测。"""
        try:
            import httpx  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    def health(self) -> dict:
        started = time.perf_counter()
        settings = get_settings()
        try:
            with httpx.Client(timeout=HEALTH_TIMEOUT) as client:
                resp = client.get(f"{settings.mineru_api_uri.rstrip('/')}/openapi.json")
                ok = resp.status_code == 200 and "/file_parse" in resp.text
            available = ok
            detail = "MinerU /file_parse 可达" if ok else "MinerU 未就绪：/openapi.json 缺少 /file_parse"
            status = "ok" if ok else "unavailable"
        except httpx.HTTPError as exc:
            available = False
            status = "unreachable"
            detail = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            available = False
            status = "error"
            detail = f"{type(exc).__name__}: {exc}"
        return {
            "name": self.name,
            "available": available,
            "status": status,
            "detail": detail,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "checked_at": time.time(),
            "formats": list(self.supported_extensions),
        }

    def parse(self, src: Path, out_dir: Path, params: dict) -> ParsedDocument:
        src = Path(src)
        if src.suffix.lower() not in self.supported_extensions:
            raise UnsupportedFormatError(
                f"MinerU 不支持 {src.suffix or '无扩展名'}；支持 pdf/图片"
            )
        if not src.exists():
            raise ParserError(f"文件不存在: {src}")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        settings = get_settings()
        uri = settings.mineru_api_uri.rstrip("/")
        timeout = float(params.get("timeout_seconds", settings.mineru_timeout_seconds) or 600)
        payload = {
            "lang_list": "ch",
            "backend": params.get("mineru_backend", "hybrid-auto-engine"),
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "image_analysis": "true",
            "start_page_id": "0",
            "end_page_id": "99999",
            "return_md": "true",
            "response_format_zip": "true",
            "return_images": "true",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                with open(src, "rb") as file_obj:
                    files = {"files": (src.name, file_obj, "application/octet-stream")}
                    resp = client.post(f"{uri}/file_parse", files=files, data=payload)
        except httpx.TimeoutException as exc:
            raise ParserError(f"mineru timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ParserError(f"mineru connection_error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ParserError(f"mineru http_error: {type(exc).__name__}: {exc}") from exc

        if resp.status_code != 200:
            raise ParserError(f"mineru http_{resp.status_code}: {resp.text[:300]}")

        zip_path = out / "mineru_result.zip"
        try:
            # 流式写临时文件以避内存峰值，但仍受 ZIP 安全上限约束
            zip_path.write_bytes(resp.content)
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"mineru 响应写入失败: {type(exc).__name__}: {exc}") from exc
        try:
            extracted = safe_extract_zip(zip_path, out / "_mineru_extract")
        except UnsafeArchiveError as exc:
            raise ParserError(f"mineru ZIP 不安全: {exc}") from exc
        except (zipfile.BadZipFile, OSError) as exc:
            raise ParserError(f"mineru 响应的 ZIP 无效: {type(exc).__name__}: {exc}") from exc

        md_path = self._pick_markdown(extracted, out / "_mineru_extract")
        if md_path is None:
            raise ParserError("mineru 结果中没有 full.md / Markdown 文件")
        markdown = md_path.read_text(encoding="utf-8", errors="replace")

        images, markdown = self._collect_and_rewrite(extracted, markdown, out)

        page_count = self._count_pages(markdown)
        warnings = [] if markdown.strip() else ["MinerU 未返回文本内容"]
        return ParsedDocument(
            markdown=markdown,
            assets=images,
            engine=self.name,
            page_count=page_count,
            warnings=warnings,
        )

    @staticmethod
    def _pick_markdown(files: list[Path], base: Path) -> Path | None:
        """优先 full.md，其次任意 .md；按目录层级排序保证确定性。"""
        md_files = [f for f in files if f.suffix.lower() == ".md"]
        if not md_files:
            return None
        for f in md_files:
            if f.name == "full.md":
                return f
        md_files.sort(key=lambda p: len(p.relative_to(base).parts))
        return md_files[0]

    def _collect_and_rewrite(
        self, files: list[Path], markdown: str, out: Path
    ) -> tuple[list[ParsedAsset], str]:
        """把解压图片收集到 out/assets，并把 Markdown 图片链接改写为相对 assets 路径。"""
        out = Path(out)
        images, mapping = self._collect_images(files, out)
        rewritten = markdown
        for original, rel in mapping.items():
            pattern = r"!\[([^\]]*)\]\(\s*" + re.escape(original) + r"\s*\)"
            rewritten = re.sub(
                pattern, lambda m, rel=rel: f"![{m.group(1)}]({rel})", rewritten
            )
        return images, rewritten

    def _collect_images(self, files: list[Path], out: Path) -> tuple[list[ParsedAsset], dict[str, str]]:
        result: list[tuple[Path, str]] = collect_images(files, out)
        assets: list[ParsedAsset] = []
        mapping: dict[str, str] = {}
        for src_path, rel in result:
            name = Path(rel).name
            assets.append(ParsedAsset(kind="image", name=name, rel_path=rel))
            mapping.setdefault(f"images/{name}", rel)
            mapping.setdefault(f"./images/{name}", rel)
        return assets, mapping

    @staticmethod
    def _count_pages(markdown: str) -> int | None:
        pages = re.findall(r"(?m)^#{1,6}\s+第\s*\d+\s*页\s*$", markdown)
        return len(pages) or None


__all__ = ["MinerUParser"]