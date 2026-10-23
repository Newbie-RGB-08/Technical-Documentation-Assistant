"""上传文档异步管道（PRD 7.10 / P4）。

执行器默认单线程，保证 OCR/Office 解析不会并发打爆内存；每个文件独立
try/except，状态严格落在 upload_files 中，启动时由 ``recover()`` 收敛。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Callable

from app.config import get_settings
from app.core.rich_chunker import chunk_uploaded_document
from app.parsers.base import ParsedDocument, ParserError
from app.parsers.native import (
    IMAGE_EXTENSIONS,
    pdf_page_count,
    pdf_text_chars_per_page,
)
from app.parsers.registry import get_parser, get_registry
from app.services.asset_store import (
    EmptyFileError,
    FileTooLargeError,
    UploadAssetStore,
    UploadStorageError,
)
from app.services.ingest_pipeline import ingest_files, prepare_chunks
from app.services.note_scanner import normalize_file_path
from app.services.upload_store import UploadStore

logger = logging.getLogger(__name__)


class UploadPipelineError(RuntimeError):
    pass


class ParseFailure(UploadPipelineError):
    pass


class IndexFailure(UploadPipelineError):
    pass


# PRD 7.5：扫描 PDF 优先 MinerU，其次 PaddleX，最后本地 RapidOCR；native 兜底。
PDF_FALLBACK_ORDER = ("mineru", "paddlex", "rapidocr", "native")
IMAGE_FALLBACK_ORDER = ("rapidocr", "paddlex", "mineru")
MIN_ENGINE_OUTPUT_CHARS = 50


def _is_available(name: str, available: Callable[[str], bool] | None) -> bool:
    try:
        return bool(available(name)) if available else bool(get_registry().available(name))
    except Exception:  # noqa: BLE001 - 单个引擎探测失败不能阻断路由
        return False


def choose_engine_candidates(
    src: Path | str,
    requested: str = "auto",
    *,
    available: Callable[[str], bool] | None = None,
    text_chars_per_page: float | None = None,
    min_text_chars: int | None = None,
) -> list[str]:
    """按请求引擎/文件类型/PDF 文本层返回尝试顺序。

    ``auto``：
    - 普通数字文档 → native；
    - 文本层 >= 阈值的 PDF → native，之后 OCR 兜底；
    - 扫描 PDF → mineru → paddlex → rapidocr → native；
    - 图片 → rapidocr → paddlex → mineru。
    显式指定引擎时只返回该引擎，绝不自动降级。
    """
    requested = (requested or "auto").strip().lower()
    ext = Path(str(src)).suffix.lower()
    if requested != "auto":
        return [requested]
    if ext in IMAGE_EXTENSIONS:
        candidates = [name for name in IMAGE_FALLBACK_ORDER if _is_available(name, available)]
        return candidates or ["rapidocr"]
    if ext == ".pdf":
        threshold = (
            int(min_text_chars)
            if min_text_chars is not None
            else get_settings().upload_pdf_text_min_chars
        )
        chars = (
            float(text_chars_per_page)
            if text_chars_per_page is not None
            else pdf_text_chars_per_page(src)
        )
        if chars >= threshold:
            return ["native"] + [
                name
                for name in PDF_FALLBACK_ORDER
                if name != "native" and _is_available(name, available)
            ]
        return [
            name for name in PDF_FALLBACK_ORDER if _is_available(name, available)
        ] or ["native"]
    return ["native"]


def markdown_text_length(markdown: str) -> int:
    """计算有效文本字符数；排除 ``## 第 N 页`` 标题与空白。"""
    text = re.sub(r"(?m)^## 第 \d+ 页\s*$", "", markdown or "")
    return len(re.sub(r"\s+", "", text))


def _parse_with_timeout(
    parser,
    src: Path,
    out_dir: Path,
    params: dict,
    timeout: float | None,
) -> ParsedDocument:
    if timeout is None:
        return parser.parse(src, out_dir, params)
    if timeout <= 0:
        raise TimeoutError("超过单文件解析总时限")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parse-timeout")
    future = executor.submit(parser.parse, src, out_dir, params)
    try:
        return future.result(timeout=timeout)
    finally:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


def parse_with_fallback(
    src: Path | str,
    out_dir: Path | str,
    candidates: list[str],
    *,
    params: dict | None = None,
    parser_getter: Callable[[str], object] | None = None,
    min_chars: int = 0,
    timeout_seconds: float | None = None,
) -> ParsedDocument:
    """按候选顺序解析；空输出/异常/超时继续下一个，全部失败抛 ParseFailure。"""
    if not candidates:
        raise ParseFailure("解析失败：没有可用的解析引擎")
    getter = parser_getter or get_parser
    src = Path(src)
    out_dir = Path(out_dir)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
    attempts: list[str] = []
    for name in candidates:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            attempts.append(f"{name}: 超过单文件解析总时限")
            break
        try:
            parser = getter(name)
            parsed = _parse_with_timeout(
                parser,
                src,
                out_dir,
                dict(params or {}),
                remaining,
            )
        except FuturesTimeoutError:
            attempts.append(f"{name}: 超时")
            continue
        except Exception as exc:  # noqa: BLE001 - 尝试下一个引擎
            logger.warning("解析引擎 %s 失败: %s", name, exc)
            attempts.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if min_chars and markdown_text_length(parsed.markdown) < min_chars:
            attempts.append(
                f"{name}: 输出过短({markdown_text_length(parsed.markdown)} < {min_chars})"
            )
            continue
        if attempts:
            parsed.warnings = [
                *parsed.warnings,
                "auto fallback: " + "; ".join(attempts),
            ]
        if not parsed.engine:
            parsed.engine = name
        return parsed
    raise ParseFailure(
        "解析失败；已尝试: " + ("; ".join(attempts) if attempts else "无可用引擎")
    )


class UploadPipeline:
    def __init__(
        self,
        state,
        upload_store: UploadStore,
        asset_store: UploadAssetStore | None = None,
        *,
        refresh_callback=None,
        executor: ThreadPoolExecutor | None = None,
        max_workers: int | None = None,
    ):
        self.state = state
        self.upload_store = upload_store
        self.asset_store = asset_store or UploadAssetStore()
        self.refresh_callback = refresh_callback
        settings = get_settings()
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max_workers or max(1, settings.upload_concurrency),
            thread_name_prefix="upload",
        )
        self._shutdown = False
        self._lock = threading.Lock()

    # ---- lifecycle ----
    def submit(self, task_id: str):
        with self._lock:
            if self._shutdown:
                raise UploadPipelineError("上传执行器已关闭")
            return self._executor.submit(self.process_batch, task_id)

    def submit_file(self, upload_id: str):
        with self._lock:
            if self._shutdown:
                raise UploadPipelineError("上传执行器已关闭")
            return self._executor.submit(self.process_file, upload_id, None)

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def recover(self) -> dict:
        """启动恢复：queued 重投、超时任务失败、parsed 继续索引。"""
        settings = get_settings()
        stale_parse = self.upload_store.stale_files(("parsing",), minutes=30)
        stale_index = self.upload_store.stale_files(("indexing",), minutes=30)
        for record in stale_parse:
            self.upload_store.update_file(
                record["upload_id"],
                status="failed_parse",
                error_message="解析任务超过 30 分钟未完成，已标记失败",
            )
        for record in stale_index:
            self.upload_store.update_file(
                record["upload_id"],
                status="failed_index",
                error_message="索引任务超过 30 分钟未完成，已标记失败",
            )
        task_ids: set[str] = set()
        for status in ("queued", "parsed"):
            for record in self.upload_store.list_files_by_statuses((status,)):
                if status == "parsed" and not record.get("auto_index"):
                    continue
                if record.get("task_id"):
                    task_ids.add(record["task_id"])
                else:
                    self.submit_file(record["upload_id"])
        for task_id in task_ids:
            self.submit(task_id)
        for task_id in {
            r.get("task_id")
            for r in stale_parse + stale_index
            if r.get("task_id")
        }:
            self.upload_store.refresh_task_counts(task_id)
        logger.info(
            "上传恢复：queued/parsed 批次 %s 个，超时 parsing=%s indexing=%s",
            len(task_ids),
            len(stale_parse),
            len(stale_index),
        )
        return {"tasks": len(task_ids), "stale_parse": len(stale_parse), "stale_index": len(stale_index)}

    # ---- batch / file ----
    def process_batch(self, task_id: str) -> None:
        task = self.upload_store.get_task(task_id)
        if not task:
            logger.warning("上传批次不存在: %s", task_id)
            return
        try:
            self.upload_store.update_task(task_id, status="running", error_message=None)
            for record in self.upload_store.files_for_task(task_id):
                if record["status"] in ("indexed", "failed_parse", "failed_index"):
                    continue
                if record["status"] == "parsed" and not record.get("auto_index"):
                    continue
                self.process_file(record["upload_id"], task)
            self.upload_store.refresh_task_counts(task_id)
        except Exception as exc:  # noqa: BLE001 - 批次级异常不能拖垮执行器
            logger.exception("上传批次异常: %s", task_id)
            self.upload_store.update_task(
                task_id, status="failed", error_message=f"批次处理失败: {exc}"
            )

    def process_file(self, upload_id: str, task: dict | None = None) -> None:
        record = self.upload_store.get_file(upload_id)
        if not record:
            return
        task = task or (
            self.upload_store.get_task(record["task_id"])
            if record.get("task_id")
            else None
        )
        status = record["status"]
        try:
            if status in ("parsed", "indexing"):
                self._index_file(record, task)
            elif status in ("queued", "parsing"):
                parsed = self._parse_file(record, task)
                if not record.get("auto_index"):
                    self.upload_store.update_file(
                        upload_id,
                        status="parsed",
                        progress=60,
                        error_message=None,
                    )
                    return
                self._index_file(
                    self.upload_store.get_file(upload_id) or record,
                    task,
                    parsed=parsed,
                )
        except ParseFailure as exc:
            self._fail_parse(record, str(exc))
        except IndexFailure as exc:
            self._fail_index(record, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("上传文件处理异常: %s", upload_id)
            if status in ("indexing", "parsed"):
                self._fail_index(record, f"{type(exc).__name__}: {exc}")
            else:
                self._fail_parse(record, f"{type(exc).__name__}: {exc}")
        finally:
            if record.get("task_id"):
                self.upload_store.refresh_task_counts(record["task_id"])

    def _parse_file(self, record: dict, task: dict | None) -> ParsedDocument:
        settings = get_settings()
        upload_id = record["upload_id"]
        self.upload_store.update_file(
            upload_id, status="parsing", progress=5, error_message=None
        )
        try:
            src = self.asset_store.original_path(upload_id, record.get("stored_path"))
            self._preflight(record, src)
            requested = (task or {}).get("engine") or "auto"
            ext = Path(record.get("original_filename") or src.name).suffix.lower()
            if not ext and record.get("doc_format"):
                ext = "." + str(record["doc_format"]).lower().lstrip(".")
            candidates = choose_engine_candidates(
                src,
                requested,
                min_text_chars=settings.upload_pdf_text_min_chars,
            )
            self.upload_store.update_file(upload_id, progress=10)
            self.asset_store.clear_parsed(upload_id)
            parsed = parse_with_fallback(
                src,
                self.asset_store.upload_dir(upload_id),
                candidates,
                params={
                    "max_images": settings.upload_max_images,
                    "max_pages": settings.upload_max_pages,
                    "image_ocr": settings.upload_image_ocr,
                },
                min_chars=MIN_ENGINE_OUTPUT_CHARS
                if ext == ".pdf" or ext in IMAGE_EXTENSIONS
                else 0,
                timeout_seconds=settings.upload_parse_timeout_seconds,
            )
            if len(parsed.markdown) > settings.upload_max_markdown_chars:
                raise ParseFailure(
                    "Markdown 超过上限 "
                    f"({len(parsed.markdown)} > {settings.upload_max_markdown_chars} 字符)"
                )
            markdown_path = self.asset_store.write_markdown(upload_id, parsed.markdown)
            self.upload_store.replace_assets(
                upload_id,
                [
                    {
                        "asset_type": asset.kind,
                        "name": asset.name,
                        "rel_path": asset.rel_path,
                        "page_no": asset.page_no,
                        "caption": asset.caption,
                        "ocr_text": asset.ocr_text,
                    }
                    for asset in parsed.assets
                ],
            )
            self.upload_store.update_file(
                upload_id,
                status="parsed",
                progress=60,
                parse_engine=parsed.engine or candidates[0],
                markdown_path=str(markdown_path),
                error_message=None,
            )
            return parsed
        except ParseFailure:
            raise
        except ParserError as exc:
            raise ParseFailure(f"解析失败: {exc}") from exc
        except (FileTooLargeError, EmptyFileError, UploadStorageError) as exc:
            raise ParseFailure(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ParseFailure(f"解析失败: {type(exc).__name__}: {exc}") from exc

    def _index_file(
        self,
        record: dict,
        task: dict | None,
        *,
        parsed: ParsedDocument | None = None,
    ) -> None:
        settings = get_settings()
        upload_id = record["upload_id"]
        markdown_path = self.asset_store.markdown_path(upload_id)
        if not markdown_path.exists():
            raise IndexFailure("解析后的 Markdown 不存在，请先重新解析")
        markdown = markdown_path.read_text(encoding="utf-8")
        if len(markdown) > settings.upload_max_markdown_chars:
            raise IndexFailure(
                "Markdown 超过上限 "
                f"({len(markdown)} > {settings.upload_max_markdown_chars} 字符)"
            )
        self.upload_store.update_file(
            upload_id, status="indexing", progress=60, error_message=None
        )
        file_path = normalize_file_path(record.get("stored_path") or "")
        title = Path(record["original_filename"]).stem
        file_record = {
            "file_path": file_path,
            "source_type": "uploaded_doc",
            "title": title,
            "tags": [],
            "content": markdown,
            "origin": "upload",
        }
        assets = self.upload_store.list_assets(upload_id)
        extra_metadata = {
            "upload_id": upload_id,
            "doc_format": record["doc_format"],
            "original_filename": record["original_filename"],
            "parse_engine": record.get("parse_engine") or "",
            "_assets": assets,
        }
        try:
            # 先按同一参数试切，超限时在任何写库动作前失败。
            preview = prepare_chunks(
                file_record,
                chunk_size=record.get("chunk_size") or settings.chunk_size,
                chunk_overlap=record.get("chunk_overlap") or settings.chunk_overlap,
                chunker=chunk_uploaded_document,
                extra_metadata=extra_metadata,
            )
            if len(preview) > settings.upload_max_chunks:
                raise IndexFailure(
                    f"切片数量超过上限 ({len(preview)} > {settings.upload_max_chunks})"
                )
            self.upload_store.update_file(upload_id, progress=75)
            summary = ingest_files(
                [file_record],
                self.state.chroma,
                self.state.metadata,
                force_reingest=True,
                chunk_size=record.get("chunk_size") or settings.chunk_size,
                chunk_overlap=record.get("chunk_overlap") or settings.chunk_overlap,
                chunker=chunk_uploaded_document,
                extra_metadata=extra_metadata,
            )
            if summary["failed"] or summary["ingested"] != 1:
                errors = "; ".join(str(e.get("error")) for e in summary["errors"])
                raise IndexFailure(errors or "索引失败")
            doc = self.state.metadata.get_document(file_path)
            if not doc:
                raise IndexFailure("索引完成后未找到文档元数据")
            self.upload_store.update_file(
                upload_id,
                status="indexed",
                progress=100,
                document_id=doc["id"],
                chunk_count=doc["chunk_count"],
                error_message=None,
            )
            self._refresh()
        except IndexFailure:
            raise
        except Exception as exc:  # noqa: BLE001
            # 尽力清掉可能写了一半的向量/元数据，避免孤儿数据。
            self._cleanup_index(file_path)
            raise IndexFailure(f"索引失败: {type(exc).__name__}: {exc}") from exc

    # ---- failure / cleanup ----
    def _fail_parse(self, record: dict, message: str) -> None:
        self.upload_store.update_file(
            record["upload_id"],
            status="failed_parse",
            progress=0,
            error_message=message,
        )
        try:
            self.asset_store.clear_parsed(record["upload_id"])
            self.upload_store.delete_assets(record["upload_id"])
        except Exception:  # noqa: BLE001
            logger.warning("清理解析半成品失败: %s", record["upload_id"], exc_info=True)

    def _fail_index(self, record: dict, message: str) -> None:
        self.upload_store.update_file(
            record["upload_id"],
            status="failed_index",
            progress=75,
            error_message=message,
        )
        self._cleanup_index(normalize_file_path(record.get("stored_path") or ""))

    def _cleanup_index(self, file_path: str) -> None:
        if not file_path:
            return
        try:
            self.state.chroma.delete_by_metadata(
                "uploaded_doc", {"file_path": file_path}
            )
        except Exception:  # noqa: BLE001
            logger.warning("清理上传向量失败: %s", file_path, exc_info=True)
        try:
            self.state.metadata.delete_document(file_path)
        except Exception:  # noqa: BLE001
            logger.warning("清理上传元数据失败: %s", file_path, exc_info=True)

    def _refresh(self) -> None:
        if self.refresh_callback is None:
            return
        try:
            self.refresh_callback(self.state)
        except Exception:  # noqa: BLE001
            logger.warning("上传后刷新索引失败", exc_info=True)

    # ---- preflight / engine ----
    def _preflight(self, record: dict, src: Path) -> None:
        settings = get_settings()
        if not src.exists():
            raise ParseFailure("原文件不存在")
        size = src.stat().st_size
        limit = settings.upload_max_mb * 1024 * 1024
        if size > limit:
            raise ParseFailure(f"文件超过 {settings.upload_max_mb} MB")
        if record.get("doc_format") == "pdf":
            try:
                pages = pdf_page_count(src)
            except ParserError as exc:
                raise ParseFailure(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise ParseFailure(f"PDF 预检失败: {exc}") from exc
            if pages <= 0:
                raise ParseFailure("PDF 没有页面")
            if pages > settings.upload_max_pages:
                raise ParseFailure(
                    f"PDF 页数超过上限 ({pages} > {settings.upload_max_pages})"
                )

    # ---- route helpers ----
    def prepare_reparse(self, upload_id: str, task_id: str) -> dict:
        record = self.upload_store.get_file(upload_id)
        if not record:
            raise UploadPipelineError("上传记录不存在")
        self._cleanup_index(normalize_file_path(record.get("stored_path") or ""))
        self.asset_store.clear_parsed(upload_id)
        self.upload_store.delete_assets(upload_id)
        return self.upload_store.update_file(
            upload_id,
            task_id=task_id,
            status="queued",
            progress=0,
            parse_engine=None,
            markdown_path=None,
            document_id=None,
            chunk_count=0,
            error_message=None,
        )

    def prepare_reindex(self, upload_id: str, task_id: str) -> dict:
        record = self.upload_store.get_file(upload_id)
        if not record:
            raise UploadPipelineError("上传记录不存在")
        if not self.asset_store.markdown_path(upload_id).exists():
            raise UploadPipelineError("解析后的 Markdown 不存在，请先重新解析")
        return self.upload_store.update_file(
            upload_id,
            task_id=task_id,
            status="parsed",
            progress=50,
            auto_index=True,
            error_message=None,
        )

    def delete(self, upload_id: str) -> dict | None:
        record = self.upload_store.get_file(upload_id)
        if not record:
            return None
        file_path = normalize_file_path(record.get("stored_path") or "")
        self._cleanup_index(file_path)
        removed = self.upload_store.delete_file(upload_id)
        try:
            self.asset_store.delete_upload(upload_id)
        except FileNotFoundError:
            pass
        self._refresh()
        return removed
