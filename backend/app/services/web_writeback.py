"""网页知识本地化写回（PRD E.10）。

四层去重（URL 规范化 / 内容哈希 / 已有文档 / 队列内 URL）、质量门、
确定性落盘、复用入库管道、后台串行队列与索引刷新回调。
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit

from app.config import get_settings
from app.services.ingest_pipeline import ingest_files, prepare_chunks  # noqa: F401（供测试替换 + 真实写回使用）
from app.services.web_fetcher import FetchedPage

logger = logging.getLogger(__name__)

TRACKING_PARAMS = {"gclid", "fbclid", "mc_cid", "mc_eid", "igshid"}
DEFAULT_PORTS = {"http": 80, "https": 443}
MIN_CONTENT_CHARS = 300
MAX_CONTENT_CHARS = 200_000
BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata",
    "instance-data",
}


def _unsafe_host(host: str) -> bool:
    """写回前的轻量主机校验：拒绝回环/私网 IP 字面量与内部域名。"""
    host = str(host or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".internal"):
        return True
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def normalize_url(url: str) -> str:
    """小写 host、去 fragment/跟踪参数/默认端口/尾斜杠，query 排序。"""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    port = parsed.port
    netloc = f"[{host}]" if ":" in host else host
    if port and port != DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{port}"

    path = parsed.path.rstrip("/")
    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    pairs.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def content_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@dataclass
class WritebackResult:
    written: bool
    reason: str = ""
    file_path: str = ""
    document_id: int | None = None
    chunk_count: int = 0
    url: str = ""


class WebWriteback:
    def __init__(
        self,
        *,
        vector_store=None,
        metadata_store=None,
        tech_docs_dir: str | Path | None = None,
        on_ingested: Callable[[dict], None] | None = None,
        trace_recorder: Callable[[dict], None] | None = None,
        min_relevance: float | None = None,
        daily_chunk_cap: int | None = None,
        cache_ttl_days: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        settings = get_settings()
        self.settings = settings
        self.vector_store = vector_store
        self.metadata_store = metadata_store
        base = Path(tech_docs_dir or settings.tech_docs_dir)
        self.web_dir = base / "web"
        self.on_ingested = on_ingested
        self.trace_recorder = trace_recorder
        self.min_relevance = float(
            settings.web_writeback_min_relevance if min_relevance is None else min_relevance
        )
        self.daily_chunk_cap = int(
            settings.web_writeback_daily_chunk_cap if daily_chunk_cap is None else daily_chunk_cap
        )
        self.cache_ttl_days = int(
            settings.web_cache_ttl_days if cache_ttl_days is None else cache_ttl_days
        )
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._seen_urls: set[str] = set()
        self._daily_date = ""
        self._daily_chunks = 0

    # ---- 质量门 ----
    def check(
        self, page: FetchedPage, *, relevance: float
    ) -> tuple[bool, str, str]:
        """返回 (是否允许, 原因, 规范化 URL)。"""
        canonical = normalize_url(page.url)
        if not canonical:
            return False, "unsupported_url", ""
        if _unsafe_host(urlparse(canonical).hostname or ""):
            return False, "unsafe_host", canonical
        if not _single_line(page.title):
            return False, "empty_title", canonical
        length = len((page.markdown or "").strip())
        if length < MIN_CONTENT_CHARS or length > MAX_CONTENT_CHARS:
            return False, "content_length", canonical
        if float(relevance) < self.min_relevance:
            return False, "low_relevance", canonical
        self._roll_daily()
        if self._daily_chunks >= self.daily_chunk_cap:
            return False, "daily_cap", canonical
        return True, "", canonical

    def _roll_daily(self) -> None:
        today = self._clock().date().isoformat()
        if today != self._daily_date:
            self._daily_date = today
            self._daily_chunks = 0

    # ---- 落盘内容 ----
    def build_document(
        self, page: FetchedPage, *, query: str
    ) -> tuple[str, str, dict]:
        """返回 (相对/绝对路径, 完整 Markdown, front-matter 元数据)。"""
        canonical = normalize_url(page.url) or page.url
        host = (urlparse(canonical).hostname or "web").lower()
        url_hash = content_sha256(canonical)[:8]
        file_path = self.web_dir / f"{host}-{url_hash}.md"

        now = self._clock()
        retrieved_at = page.retrieved_at or now.isoformat(timespec="seconds")
        try:
            base_time = datetime.fromisoformat(retrieved_at)
        except ValueError:
            base_time = now
        expires_at = (base_time + timedelta(days=self.cache_ttl_days)).isoformat(
            timespec="seconds"
        )

        front_matter = {
            "title": _single_line(page.title),
            "source_url": page.url,
            "canonical_url": canonical,
            "source_type": "tech_doc",
            "origin": "web_search",
            "search_query": _single_line(query),
            "provider": page.provider or "",
            "retrieved_at": retrieved_at,
            "expires_at": expires_at,
        }
        fm_lines = ["---"] + [f"{key}: {value}" for key, value in front_matter.items()] + ["---"]
        trace_block = (
            f"\n\n---\n\n> 溯源：来源 {page.url}，检索时间 {retrieved_at}，"
            f"检索词：{_single_line(query) or '（无）'}。\n"
        )
        body = (page.markdown or "").strip()
        content = "\n".join(fm_lines) + "\n\n" + body + trace_block
        return str(file_path), content, front_matter

    def _existing_document(self, file_path: str, canonical: str) -> dict | None:
        if self.metadata_store is None:
            return None
        doc = self.metadata_store.get_document(str(file_path))
        if doc:
            return doc
        getter = getattr(self.metadata_store, "get_document_by_source_url", None)
        if getter is not None:
            return getter(canonical)
        return None

    # ---- 写回 ----
    def write(self, page: FetchedPage, *, query: str, relevance: float) -> WritebackResult:
        allowed, reason, canonical = self.check(page, relevance=relevance)
        if not allowed:
            self._trace({"url": page.url, "written": False, "reason": reason})
            return WritebackResult(False, reason=reason, url=page.url)

        if canonical in self._seen_urls:
            self._trace({"url": page.url, "written": False, "reason": "duplicate_url"})
            return WritebackResult(False, reason="duplicate_url", url=page.url)

        file_path, content, front_matter = self.build_document(page, query=query)
        digest = content_sha256(content)

        existing = self._existing_document(file_path, canonical)
        if existing and existing.get("content_hash") == digest:
            self._seen_urls.add(canonical)
            self._trace({"url": page.url, "written": False, "reason": "duplicate_content"})
            return WritebackResult(
                False, reason="duplicate_content", file_path=file_path, url=page.url
            )

        record = {
            "file_path": file_path,
            "source_type": "tech_doc",
            "title": front_matter["title"],
            "tags": ["web"],
            "created_at": front_matter["retrieved_at"],
            "updated_at": front_matter["retrieved_at"],
            "source_url": page.url,
            "canonical_url": canonical,
            "origin": "web_search",
            "retrieved_at": front_matter["retrieved_at"],
            "expires_at": front_matter["expires_at"],
            "search_query": front_matter["search_query"],
            "provider": page.provider or "",
            "content": content,
        }

        estimated_chunks = len(
            prepare_chunks(
                record,
                chunk_size=self.settings.chunk_size,
                chunk_overlap=self.settings.chunk_overlap,
            )
        )
        self._roll_daily()
        if self._daily_chunks + estimated_chunks > self.daily_chunk_cap:
            self._trace({"url": page.url, "written": False, "reason": "daily_cap"})
            return WritebackResult(False, reason="daily_cap", url=page.url)

        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_text(content, encoding="utf-8")

        if self.vector_store is None or self.metadata_store is None:
            self._seen_urls.add(canonical)
            self._trace({"url": page.url, "written": True, "file_path": file_path,
                         "reason": "written"})
            return WritebackResult(
                True, reason="written", file_path=file_path,
                chunk_count=estimated_chunks, url=page.url,
            )

        summary = ingest_files(
            [record],
            self.vector_store,
            self.metadata_store,
            force_reingest=bool(existing),
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
        )
        if summary["failed"]:
            reason = "ingest_failed"
            self._trace({"url": page.url, "written": False, "reason": reason})
            return WritebackResult(False, reason=reason, file_path=file_path, url=page.url)

        self._seen_urls.add(canonical)
        self._daily_chunks += summary["total_chunks"]
        document = self.metadata_store.get_document(file_path)
        document_id = document["id"] if document else None
        if self.on_ingested is not None:
            try:
                self.on_ingested(
                    {
                        "file_path": file_path,
                        "document_id": document_id,
                        "chunk_count": summary["total_chunks"],
                        "origin": "web_search",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 刷新失败不影响写回
                logger.warning("写回后刷新索引失败: %s", exc)
        self._trace({"url": page.url, "written": True, "file_path": file_path,
                     "reason": "written"})
        return WritebackResult(
            True,
            reason="written",
            file_path=file_path,
            document_id=document_id,
            chunk_count=summary["total_chunks"],
            url=page.url,
        )

    def _trace(self, payload: dict) -> None:
        if self.trace_recorder is None:
            return
        try:
            self.trace_recorder({"event": "web_writeback", **payload})
        except Exception:  # noqa: BLE001
            pass


class WebWritebackQueue:
    """容量受限的后台串行写回队列。"""

    def __init__(
        self,
        writeback: WebWriteback,
        *,
        capacity: int | None = None,
        auto_start: bool = False,
    ):
        settings = get_settings()
        self.writeback = writeback
        self.capacity = int(
            settings.web_writeback_queue_size if capacity is None else capacity
        )
        self._queue: queue.Queue = queue.Queue(maxsize=self.capacity)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.processed = 0
        self.dropped = 0
        if auto_start:
            self.start()

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="web-writeback", daemon=True)
        self._worker.start()

    def put(self, job: dict) -> bool:
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            self.dropped += 1
            logger.warning("写回队列已满（%s），丢弃任务: %s", self.capacity, job.get("page").url
                           if isinstance(job.get("page"), FetchedPage) else "")
            return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(job)
            finally:
                self._queue.task_done()

    def _process(self, job: dict) -> WritebackResult:
        self.processed += 1
        try:
            return self.writeback.write(
                job["page"], query=job.get("query", ""), relevance=job.get("relevance", 0.0)
            )
        except Exception as exc:  # noqa: BLE001 - 单任务失败不影响回答
            logger.warning("写回任务失败: %s", exc)
            if self.writeback.trace_recorder is not None:
                try:
                    self.writeback.trace_recorder(
                        {"event": "web_writeback", "reason": "exception", "error": str(exc)}
                    )
                except Exception:  # noqa: BLE001
                    pass
            return WritebackResult(False, reason="exception")

    def drain(self) -> int:
        """同步处理完当前积压任务（测试与关闭流程使用）。"""
        count = 0
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._process(job)
                count += 1
            finally:
                self._queue.task_done()
        return count

    def join(self) -> None:
        self._queue.join()

    def stop(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=5)
            self._worker = None

    @property
    def qsize(self) -> int:
        return self._queue.qsize()
