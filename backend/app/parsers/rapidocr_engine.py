"""RapidOCR 本地解析器（PRD 7.4.2 / P6）。

依赖全部延迟导入：即使未安装 rapidocr/onnxruntime/pypdfium2，后端仍可启动，
仅该引擎健康检查显示不可用。PDF 逐页渲染后 OCR，输出 ``## 第 N 页``。
"""
from __future__ import annotations

import importlib
import logging
import tempfile
import threading
import time
from pathlib import Path

from app.config import get_settings
from app.parsers.base import (
    DocumentParser,
    ParsedDocument,
    ParserError,
    UnsupportedFormatError,
)
from app.parsers.native import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)


class RapidOCRParser(DocumentParser):
    name = "rapidocr"
    supported_extensions = (".pdf",) + IMAGE_EXTENSIONS

    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.RLock()

    # ---- health ----
    def available(self) -> bool:
        """只检查依赖 import 与模型目录可写，不加载模型。"""
        try:
            importlib.import_module("rapidocr")
            importlib.import_module("pypdfium2")
            importlib.import_module("PIL")
        except Exception:  # noqa: BLE001
            return False
        try:
            model_dir = Path(get_settings().rapidocr_model_dir)
            model_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=model_dir, delete=True):
                pass
        except Exception:  # noqa: BLE001
            return False
        return True

    def health(self) -> dict:
        started = time.perf_counter()
        available = self.available()
        settings = get_settings()
        return {
            "name": self.name,
            "available": available,
            "status": "ok" if available else "unavailable",
            "detail": (
                f"rapidocr import ok; model_root_dir={settings.rapidocr_model_dir}"
                if available
                else "缺少 rapidocr/pypdfium2/Pillow，或模型目录不可写"
            ),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "checked_at": time.time(),
            "formats": list(self.supported_extensions),
        }

    # ---- lazy engine ----
    def _get_engine(self):
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                from rapidocr import RapidOCR
            except Exception as exc:  # noqa: BLE001
                raise ParserError(
                    f"RapidOCR 不可用: {type(exc).__name__}: {exc}"
                ) from exc
            settings = get_settings()
            model_dir = Path(settings.rapidocr_model_dir)
            model_dir.mkdir(parents=True, exist_ok=True)
            params = {
                "Global": {"model_root_dir": str(model_dir)},
                "Det": {
                    "engine_type": "onnxruntime",
                    "lang_type": "ch",
                    "model_type": "mobile",
                    "ocr_version": "PP-OCRv5",
                },
                "Cls": {"engine_type": "onnxruntime"},
                "Rec": {
                    "engine_type": "onnxruntime",
                    "lang_type": "ch",
                    "model_type": "mobile",
                    "ocr_version": "PP-OCRv5",
                },
            }
            try:
                self._engine = RapidOCR(params=params)
            except Exception as exc:  # noqa: BLE001
                raise ParserError(
                    f"RapidOCR 初始化失败: {type(exc).__name__}: {exc}"
                ) from exc
            return self._engine

    @staticmethod
    def _result_text(result) -> str:
        if result is None:
            return ""
        txts = getattr(result, "txts", None)
        if txts is None and isinstance(result, dict):
            txts = result.get("txts")
        if not txts:
            return ""
        return "\n".join(str(item).strip() for item in txts if str(item).strip())

    def _ocr_image(self, image) -> str:
        with self._lock:
            engine = self._get_engine()
            try:
                result = engine(image)
            except Exception as exc:  # noqa: BLE001
                raise ParserError(f"RapidOCR 识别失败: {type(exc).__name__}: {exc}") from exc
        return self._result_text(result)

    # ---- parse ----
    def parse(self, src: Path, out_dir: Path, params: dict) -> ParsedDocument:
        src = Path(src)
        ext = src.suffix.lower()
        if ext not in self.supported_extensions:
            raise UnsupportedFormatError(
                f"RapidOCR 不支持 {ext or '无扩展名'}；支持: "
                + ", ".join(self.supported_extensions)
            )
        if not src.exists():
            raise ParserError(f"文件不存在: {src}")
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        max_pages = int(params.get("max_pages") or 0)
        if ext == ".pdf":
            return self._parse_pdf(src, max_pages=max_pages)
        return self._parse_image(src)

    def _parse_image(self, src: Path) -> ParsedDocument:
        try:
            from PIL import Image
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"Pillow 不可用: {exc}") from exc
        try:
            with Image.open(src) as image:
                text = self._ocr_image(image.convert("RGB"))
        except ParserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"图片打开失败: {type(exc).__name__}: {exc}") from exc
        warnings = [] if text.strip() else ["OCR 未识别到文本"]
        return ParsedDocument(
            markdown=text.strip(),
            engine=self.name,
            page_count=1,
            warnings=warnings,
        )

    def _parse_pdf(self, src: Path, *, max_pages: int = 0) -> ParsedDocument:
        try:
            import pypdfium2 as pdfium
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"pypdfium2 不可用: {exc}") from exc
        try:
            document = pdfium.PdfDocument(str(src))
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"PDF 打开失败: {type(exc).__name__}: {exc}") from exc

        page_count = 0
        warnings: list[str] = []
        pages_markdown: list[str] = []
        try:
            page_count = len(document)
            if page_count <= 0:
                raise ParserError("PDF 没有页面")
            if max_pages and page_count > max_pages:
                raise ParserError(f"PDF 页数超过上限 ({page_count} > {max_pages})")
            for index in range(page_count):
                page = document[index]
                bitmap = None
                try:
                    bitmap = page.render(scale=2)
                    image = bitmap.to_pil()
                    try:
                        text = self._ocr_image(image)
                    finally:
                        close = getattr(image, "close", None)
                        if close:
                            close()
                    if not text.strip():
                        warnings.append(f"第 {index + 1} 页未识别到文本")
                    pages_markdown.append(
                        f"## 第 {index + 1} 页\n\n{text.strip()}".rstrip()
                    )
                except ParserError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise ParserError(
                        f"第 {index + 1} 页渲染/OCR 失败: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                finally:
                    if bitmap is not None:
                        close = getattr(bitmap, "close", None)
                        if close:
                            close()
                    close = getattr(page, "close", None)
                    if close:
                        close()
        finally:
            close = getattr(document, "close", None)
            if close:
                close()

        markdown = "\n\n".join(pages_markdown).strip()
        return ParsedDocument(
            markdown=markdown,
            engine=self.name,
            page_count=page_count,
            warnings=warnings,
        )


__all__ = ["RapidOCRParser"]
