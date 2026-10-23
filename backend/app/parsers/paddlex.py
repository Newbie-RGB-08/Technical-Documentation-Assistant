"""PaddleX（PP-StructureV3）HTTP 解析适配器（PRD 7.4.3 / P7-2）。

调用 `POST {uri}/layout-parsing`（JSON base64），取
``result.layoutParsingResults[].markdown.text`` 逐页拼接。首版只保留文本/表格，
不处理 ``markdown.images``。健康用 30s 缓存（走 registry ttl）。
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

import httpx

from app.config import get_settings
from app.parsers.base import (
    DocumentParser,
    ParsedDocument,
    ParserError,
    UnsupportedFormatError,
)
from app.parsers.native import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 5.0


class PaddleXParser(DocumentParser):
    name = "paddlex"
    supported_extensions = (".pdf",) + IMAGE_EXTENSIONS

    def available(self) -> bool:
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
                resp = client.get(f"{settings.paddlex_uri.rstrip('/')}/health")
                ok = resp.status_code == 200
            available = ok
            detail = "PaddleX 服务可达" if ok else f"PaddleX /health 返回 {resp.status_code}"
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
        ext = src.suffix.lower()
        if ext not in self.supported_extensions:
            raise UnsupportedFormatError(
                f"PaddleX 不支持 {ext or '无扩展名'}；支持 pdf/图片"
            )
        if not src.exists():
            raise ParserError(f"文件不存在: {src}")
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        file_type = 0 if ext == ".pdf" else 1
        b64 = base64.b64encode(src.read_bytes()).decode("ascii")
        payload = {
            "file": b64,
            "fileType": file_type,
            "useTableRecognition": True,
            "useFormulaRecognition": True,
            "useSealRecognition": False,
        }
        settings = get_settings()
        uri = settings.paddlex_uri.rstrip("/")
        timeout = float(params.get("timeout_seconds", settings.paddlex_timeout_seconds) or 300)

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(f"{uri}/layout-parsing", json=payload)
        except httpx.TimeoutException as exc:
            raise ParserError(f"paddlex timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ParserError(f"paddlex connection_error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ParserError(f"paddlex http_error: {type(exc).__name__}: {exc}") from exc

        if resp.status_code != 200:
            raise ParserError(f"paddlex http_{resp.status_code}: {resp.text[:300]}")

        data = {}
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"paddlex 响应非 JSON: {type(exc).__name__}: {exc}") from exc

        if data.get("errorCode", 0) != 0:
            raise ParserError(
                f"paddlex error_{data.get('errorCode')}: {data.get('errorMsg') or data.get('message') or '未知错误'}"
            )

        results = (data.get("result") or {}).get("layoutParsingResults") or []
        pages: list[str] = []
        warnings: list[str] = []
        for index, item in enumerate(results, start=1):
            md = (item.get("markdown") or {}).get("text") or ""
            if not md.strip():
                warnings.append(f"第 {index} 页未返回文本")
            pages.append(f"## 第 {index} 页\n\n{md.strip()}".rstrip())

        if not pages:
            raise ParserError("paddlex 返回空 layoutParsingResults")

        markdown = "\n\n".join(pages).strip() + ("\n" if pages else "")
        return ParsedDocument(
            markdown=markdown,
            engine=self.name,
            page_count=len(results),
            warnings=warnings,
        )


__all__ = ["PaddleXParser"]