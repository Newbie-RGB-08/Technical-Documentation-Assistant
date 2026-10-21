"""网页抓取：SSRF 防护、协议/大小/超时/并发限制、正文清洗（PRD E.9.2 / E.9.3）。

复用 crawler.html_to_markdown；优先使用搜索 Provider 返回的正文。
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings
from app.services.crawler import html_to_markdown
from app.services.web_search import WebResult

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; PTDRA/1.0; personal research)"
MAX_REDIRECTS = 3
ALLOWED_SCHEMES = ("http", "https")
BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata",
    "instance-data",
}
HTML_TYPES = ("text/html", "application/xhtml+xml")


class FetchError(Exception):
    pass


@dataclass
class FetchedPage:
    url: str
    title: str = ""
    markdown: str = ""
    content_type: str = "text/html"
    status: int = 200
    provider: str = ""
    snippet: str = ""
    published_at: str | None = None
    retrieved_at: str = ""
    error: str | None = None


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def _ip_is_unsafe(ip_obj) -> bool:
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def is_safe_url(url: str, resolver=None) -> bool:
    """仅 http/https，拒绝回环/私网/链路本地/保留/云元数据地址。"""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    if host in BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".internal"):
        return False
    try:
        return not _ip_is_unsafe(ipaddress.ip_address(host))
    except ValueError:
        pass

    resolver = resolver or _default_resolver
    try:
        addresses = resolver(host)
    except Exception:  # noqa: BLE001 - 解析失败视为不安全
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            if _ip_is_unsafe(ipaddress.ip_address(str(address))):
                return False
        except ValueError:
            return False
    return True


class _DomainLimiter:
    """每域名最小抓取间隔（默认 1 秒），避免对站点造成压力。"""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = max(0.0, float(min_interval))
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            delay = self.min_interval - (now - self._last.get(host, 0.0))
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _looks_like_markdown(text: str) -> bool:
    lowered = text.lstrip().lower()
    return not lowered.startswith(("<html", "<!doctype", "<body", "<div"))


def _clean_provider_content(content: str) -> str:
    if _looks_like_markdown(content):
        return content
    markdown, _title = html_to_markdown(content)
    return markdown


async def _get_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    resolver,
    max_bytes: int,
) -> tuple[str, str, str]:
    """带安全复检的 GET，返回 (html, 最终 URL, content-type)。"""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_safe_url(current, resolver):
            raise FetchError("不安全的 URL")
        async with client.stream("GET", current, follow_redirects=False) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise FetchError("重定向缺少 Location")
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise FetchError(f"HTTP {resp.status_code}")
            content_type = (resp.headers.get("content-type") or "").lower()
            if not any(kind in content_type for kind in HTML_TYPES):
                raise FetchError("非 HTML 内容")
            total = 0
            parts: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("超出单页大小上限")
                parts.append(chunk)
            encoding = resp.charset_encoding or "utf-8"
            return b"".join(parts).decode(encoding, errors="replace"), current, content_type
    raise FetchError("重定向次数过多")


async def _fetch_one(
    result: WebResult,
    *,
    client: httpx.AsyncClient,
    resolver,
    limiter: _DomainLimiter,
    max_bytes: int,
    max_chars: int,
    min_chars: int,
    cache=None,
) -> FetchedPage | None:
    if not is_safe_url(result.url, resolver):
        return None

    if cache is not None:
        cached = cache.get_page(result.url)
        if cached and len((cached.get("markdown") or "").strip()) >= min_chars:
            return FetchedPage(
                url=cached.get("url") or result.url,
                title=cached.get("title") or result.title,
                markdown=str(cached.get("markdown") or "")[:max_chars],
                provider=cached.get("provider") or result.provider,
                snippet=result.snippet,
                retrieved_at=cached.get("retrieved_at") or _now(),
            )

    page = await _download_or_use_content(
        result,
        client=client,
        resolver=resolver,
        limiter=limiter,
        max_bytes=max_bytes,
        max_chars=max_chars,
        min_chars=min_chars,
    )
    if page is not None and cache is not None:
        cache.set_page(
            {
                "url": page.url,
                "title": page.title,
                "markdown": page.markdown,
                "provider": page.provider,
                "retrieved_at": page.retrieved_at,
            }
        )
    return page


async def _download_or_use_content(
    result: WebResult,
    *,
    client: httpx.AsyncClient,
    resolver,
    limiter: _DomainLimiter,
    max_bytes: int,
    max_chars: int,
    min_chars: int,
) -> FetchedPage | None:
    if result.content and len(result.content.strip()) >= min_chars:
        markdown = _clean_provider_content(result.content)
        markdown = markdown[:max_chars]
        if len(markdown.strip()) < min_chars:
            return None
        return FetchedPage(
            url=result.url,
            title=result.title,
            markdown=markdown,
            provider=result.provider,
            snippet=result.snippet,
            published_at=result.published_at,
            retrieved_at=_now(),
        )

    host = (urlparse(result.url).hostname or "").lower()
    await limiter.wait(host)
    html, final_url, content_type = await _get_html(
        client, result.url, resolver=resolver, max_bytes=max_bytes
    )
    markdown, title = html_to_markdown(html, base_url=final_url)
    markdown = markdown[:max_chars]
    if len(markdown.strip()) < min_chars:
        return None
    return FetchedPage(
        url=final_url,
        title=title or result.title,
        markdown=markdown,
        content_type=content_type,
        provider=result.provider,
        snippet=result.snippet,
        published_at=result.published_at,
        retrieved_at=_now(),
    )


async def fetch_pages(
    results: list[WebResult],
    *,
    client: httpx.AsyncClient | None = None,
    resolver=None,
    top_n: int | None = None,
    concurrency: int | None = None,
    per_page_timeout: float | None = None,
    total_timeout: float | None = None,
    max_bytes: int | None = None,
    max_chars: int | None = None,
    min_chars: int | None = None,
    min_interval: float | None = None,
    cache=None,
) -> list[FetchedPage]:
    """并发抓取搜索结果，单页/总超时与失败隔离；全部失败返回空列表。"""
    settings = get_settings()
    top_n = int(settings.web_fetch_top_n if top_n is None else top_n)
    concurrency = max(1, int(settings.web_fetch_concurrency if concurrency is None else concurrency))
    per_page_timeout = float(
        settings.web_fetch_timeout_seconds if per_page_timeout is None else per_page_timeout
    )
    total_timeout = float(
        settings.web_fetch_total_timeout_seconds if total_timeout is None else total_timeout
    )
    max_bytes = int(settings.web_page_max_bytes if max_bytes is None else max_bytes)
    max_chars = int(settings.web_content_max_chars if max_chars is None else max_chars)
    min_chars = int(settings.web_min_content_chars if min_chars is None else min_chars)
    min_interval = float(
        settings.web_fetch_min_interval_seconds if min_interval is None else min_interval
    )

    candidates = list(results)[:top_n]
    if not candidates:
        return []

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=per_page_timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )
    limiter = _DomainLimiter(min_interval)
    semaphore = asyncio.Semaphore(concurrency)

    async def _run(result: WebResult) -> FetchedPage | None:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    _fetch_one(
                        result,
                        client=client,
                        resolver=resolver,
                        limiter=limiter,
                        max_bytes=max_bytes,
                        max_chars=max_chars,
                        min_chars=min_chars,
                        cache=cache,
                    ),
                    timeout=per_page_timeout,
                )
            except Exception as exc:  # noqa: BLE001 - 单页失败不阻断
                logger.warning("网页抓取失败 %s: %s", result.url, exc)
                return None

    tasks = [asyncio.create_task(_run(result)) for result in candidates]
    try:
        done, pending = await asyncio.wait(tasks, timeout=total_timeout)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if owns_client:
            await client.aclose()

    pages: list[FetchedPage] = []
    for task in done:
        if task.cancelled() or task.exception() is not None:
            continue
        page = task.result()
        if isinstance(page, FetchedPage):
            pages.append(page)
    return pages
