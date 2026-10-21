"""线上搜索 Provider 抽象与网关（PRD E.9.1 / E.9.4）。

- 全部用 httpx 调 REST，不引入新依赖。
- 无 Key / 未配置地址的 Provider 自动禁用。
- 网关按配置链降级；单个 Provider 失败计入熔断；结果可走缓存。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.core.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; PTDRA/1.0; personal research)"
_PROVIDER_TIMEOUT = 8.0


@dataclass
class WebResult:
    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0
    content: str | None = None
    published_at: str | None = None
    provider: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "rank": self.rank,
            "content": self.content,
            "published_at": self.published_at,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WebResult":
        return cls(
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            snippet=str(data.get("snippet") or ""),
            rank=int(data.get("rank") or 0),
            content=data.get("content"),
            published_at=data.get("published_at"),
            provider=str(data.get("provider") or ""),
        )


class WebSearchProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    async def search(self, query: str, top_k: int = 5) -> list[WebResult]: ...


def normalize_query(query: str) -> str:
    return " ".join(str(query or "").lower().split())


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str = "", base_url: str = "https://api.tavily.com/search"):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, top_k: int = 5) -> list[WebResult]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": top_k,
            "include_raw_content": True,
            "search_depth": "basic",
        }
        data = await self._post_json(self.base_url, payload)
        results: list[WebResult] = []
        for item in data.get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                WebResult(
                    url=url,
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("content") or ""),
                    rank=len(results),
                    content=item.get("raw_content") or None,
                    provider=self.name,
                )
            )
        return results[:top_k]

    async def _post_json(self, url: str, payload: dict) -> dict:
        async with httpx.AsyncClient(
            timeout=_PROVIDER_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()


class BingSearchProvider:
    name = "bing"

    def __init__(self, api_key: str = "", base_url: str = "https://api.bing.microsoft.com/v7.0/search"):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, top_k: int = 5) -> list[WebResult]:
        data = await self._get_json(
            self.base_url, params={"q": query, "count": top_k}, api_key=self.api_key
        )
        results: list[WebResult] = []
        for item in ((data.get("webPages") or {}).get("value") or []):
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                WebResult(
                    url=url,
                    title=str(item.get("name") or ""),
                    snippet=str(item.get("snippet") or ""),
                    rank=len(results),
                    published_at=item.get("datePublished"),
                    provider=self.name,
                )
            )
        return results[:top_k]

    async def _get_json(self, url: str, params: dict, api_key: str) -> dict:
        headers = {"Ocp-Apim-Subscription-Key": api_key, "User-Agent": USER_AGENT}
        async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT, headers=headers) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()


def decode_ddg_url(raw: str) -> str:
    """DuckDuckGo 结果链接可能是 //duckduckgo.com/l/?uddg=<encoded> 形式。"""
    raw = str(raw or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return raw


def parse_duckduckgo_html(html: str, limit: int = 5) -> list[WebResult]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[WebResult] = []
    for node in soup.select("a.result__a"):
        url = decode_ddg_url(node.get("href", ""))
        if not url.startswith(("http://", "https://")):
            continue
        parent = node.find_parent(class_="result") or node.parent
        snippet_node = parent.select_one(".result__snippet") if parent else None
        results.append(
            WebResult(
                url=url,
                title=node.get_text(" ", strip=True),
                snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
                rank=len(results),
                provider="duckduckgo",
            )
        )
        if len(results) >= limit:
            break
    return results


class DuckDuckGoHtmlProvider:
    name = "duckduckgo"

    def __init__(self, base_url: str = "https://html.duckduckgo.com/html/"):
        self.base_url = base_url

    def is_available(self) -> bool:
        return True

    async def search(self, query: str, top_k: int = 5) -> list[WebResult]:
        async with httpx.AsyncClient(
            timeout=_PROVIDER_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.post(self.base_url, data={"q": query})
            resp.raise_for_status()
            return parse_duckduckgo_html(resp.text, limit=top_k)


class SearXNGProvider:
    name = "searxng"

    def __init__(self, base_url: str = ""):
        self.base_url = (base_url or "").strip().rstrip("/")

    def is_available(self) -> bool:
        return bool(self.base_url)

    async def search(self, query: str, top_k: int = 5) -> list[WebResult]:
        data = await self._get_json(
            f"{self.base_url}/search", params={"q": query, "format": "json"}
        )
        results: list[WebResult] = []
        for item in data.get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                WebResult(
                    url=url,
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("content") or ""),
                    rank=len(results),
                    published_at=item.get("publishedDate"),
                    provider=self.name,
                )
            )
            if len(results) >= top_k:
                break
        return results

    async def _get_json(self, url: str, params: dict) -> dict:
        async with httpx.AsyncClient(
            timeout=_PROVIDER_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()


def build_providers(settings=None) -> list[WebSearchProvider]:
    """按配置链构建 Provider；无 Key / 未配置地址的自动跳过。"""
    settings = settings or get_settings()
    factories = {
        "tavily": lambda: TavilySearchProvider(settings.tavily_api_key),
        "bing": lambda: BingSearchProvider(settings.bing_search_api_key),
        "duckduckgo": DuckDuckGoHtmlProvider,
        "searxng": lambda: SearXNGProvider(settings.searxng_base_url),
    }
    providers: list[WebSearchProvider] = []
    for name in settings.web_search_provider_chain:
        factory = factories.get(name)
        if factory is None:
            continue
        provider = factory()
        if provider.is_available():
            providers.append(provider)
    return providers


class WebSearchGateway:
    """按链降级搜索；单个 Provider 连续失败触发熔断。"""

    def __init__(
        self,
        providers: list[WebSearchProvider] | None = None,
        *,
        cache=None,
        breaker: CircuitBreaker | None = None,
        top_k: int | None = None,
    ):
        settings = get_settings()
        self.providers = providers if providers is not None else build_providers(settings)
        self.cache = cache
        self.breaker = breaker or CircuitBreaker()
        self.top_k = int(top_k or settings.web_search_top_k)

    async def search(self, query: str, top_k: int | None = None) -> list[WebResult]:
        if not self.providers:
            return []
        limit = int(top_k or self.top_k)
        normalized = normalize_query(query)

        for provider in self.providers:
            key = f"web:{provider.name}"
            if not self.breaker.allow(key):
                continue
            if self.cache is not None:
                cached = self.cache.get_search(normalized, provider.name, limit)
                if cached is not None:
                    return [WebResult.from_dict(d) for d in cached][:limit]
            try:
                results = await provider.search(query, top_k=limit)
            except Exception as exc:  # noqa: BLE001 - 单个失败继续降级
                logger.warning("搜索 Provider '%s' 失败: %s", provider.name, exc)
                self.breaker.record_failure(key)
                continue
            self.breaker.record_success(key)
            if not results:
                continue
            if self.cache is not None:
                self.cache.set_search(normalized, provider.name, limit,
                                      [r.to_dict() for r in results])
            return results[:limit]
        return []
