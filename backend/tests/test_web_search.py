"""P3：搜索 Provider 解析、无 Key 禁用、网关降级与缓存。"""
import asyncio

import pytest

from app.config import Settings
from app.core.circuit_breaker import CircuitBreaker
from app.services.web_search import (
    BingSearchProvider,
    DuckDuckGoHtmlProvider,
    SearXNGProvider,
    TavilySearchProvider,
    WebResult,
    WebSearchGateway,
    build_providers,
    decode_ddg_url,
    parse_duckduckgo_html,
)


class FakeProvider:
    def __init__(self, name, results=None, error=None, available=True):
        self.name = name
        self.results = results or []
        self.error = error
        self.available = available
        self.calls = 0

    def is_available(self):
        return self.available

    async def search(self, query, top_k=5):
        self.calls += 1
        if self.error:
            raise self.error
        return self.results[:top_k]


class FakeCache:
    def __init__(self, cached=None):
        self.cached = cached
        self.saved = []

    def get_search(self, query, provider, top_k):
        return self.cached

    def set_search(self, query, provider, top_k, results):
        self.saved.append((query, provider, top_k, results))


def _result(url="https://example.com/a", title="A"):
    return WebResult(url=url, title=title, snippet="摘要", provider="fake")


def test_no_providers_returns_empty():
    gateway = WebSearchGateway(providers=[])
    assert asyncio.run(gateway.search("q")) == []


def test_fallback_to_next_provider():
    first = FakeProvider("tavily", error=RuntimeError("boom"))
    second = FakeProvider("bing", results=[_result()])
    breaker = CircuitBreaker(threshold=3, cooldown_seconds=60)
    gateway = WebSearchGateway([first, second], breaker=breaker)

    results = asyncio.run(gateway.search("q"))

    assert [r.url for r in results] == ["https://example.com/a"]
    assert first.calls == 1 and second.calls == 1
    assert breaker.failure_count("web:tavily") == 1
    assert breaker.failure_count("web:bing") == 0


def test_circuit_open_skips_provider():
    bad = FakeProvider("tavily", error=RuntimeError("boom"))
    good = FakeProvider("bing", results=[_result()])
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=60)
    gateway = WebSearchGateway([bad, good], breaker=breaker)

    for _ in range(3):
        asyncio.run(gateway.search("q"))

    assert bad.calls == 2  # 第三次已被熔断跳过
    assert good.calls == 3


def test_empty_results_do_not_trigger_breaker():
    empty = FakeProvider("tavily", results=[])
    fallback = FakeProvider("bing", results=[_result()])
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=60)
    gateway = WebSearchGateway([empty, fallback], breaker=breaker)

    results = asyncio.run(gateway.search("q"))

    assert results and empty.calls == 1
    assert breaker.failure_count("web:tavily") == 0


def test_cache_hit_and_store():
    provider = FakeProvider("tavily", results=[_result()])
    cached_payload = [{"url": "https://cached.example.com/x", "title": "缓存",
                       "snippet": "", "rank": 0, "provider": "tavily"}]
    cache = FakeCache(cached=cached_payload)
    gateway = WebSearchGateway([provider], cache=cache)

    results = asyncio.run(gateway.search("Query  A"))

    assert results[0].url == "https://cached.example.com/x"
    assert provider.calls == 0

    cache.cached = None
    results = asyncio.run(gateway.search("Query  A"))
    assert results[0].url == "https://example.com/a"
    assert cache.saved and cache.saved[0][0] == "query a"


def test_build_providers_without_keys_only_ddg():
    settings = Settings(
        web_search_providers="tavily,bing,duckduckgo,searxng",
        tavily_api_key="",
        bing_search_api_key="",
        searxng_base_url="",
    )
    names = [p.name for p in build_providers(settings)]
    assert names == ["duckduckgo"]


def test_decode_ddg_redirect_url():
    url = decode_ddg_url(
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2F&rut=abc"
    )
    assert url == "https://docs.python.org/3/"


def test_parse_duckduckgo_html():
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc">
        Example 文档
      </a>
      <a class="result__snippet">这是摘要内容</a>
    </div>
    """
    results = parse_duckduckgo_html(html, limit=5)
    assert len(results) == 1
    assert results[0].url == "https://example.com/doc"
    assert results[0].title == "Example 文档"
    assert results[0].snippet == "这是摘要内容"
    assert results[0].provider == "duckduckgo"


def test_tavily_parse_and_availability():
    provider = TavilySearchProvider(api_key="")
    assert provider.is_available() is False
    provider = TavilySearchProvider(api_key="key")

    async def fake_post(url, payload):
        return {
            "results": [
                {"url": "https://a.example", "title": "A", "content": "摘要A",
                 "raw_content": "完整正文"},
                {"url": "", "title": "空"},
            ]
        }

    provider._post_json = fake_post
    results = asyncio.run(provider.search("q", top_k=5))
    assert len(results) == 1
    assert results[0].url == "https://a.example"
    assert results[0].content == "完整正文"
    assert results[0].snippet == "摘要A"
    assert results[0].provider == "tavily"


def test_bing_parse():
    provider = BingSearchProvider(api_key="key")

    async def fake_get(url, params, api_key):
        return {
            "webPages": {
                "value": [
                    {"url": "https://b.example", "name": "Bing 结果", "snippet": "摘要"}
                ]
            }
        }

    provider._get_json = fake_get
    results = asyncio.run(provider.search("q"))
    assert results[0].url == "https://b.example"
    assert results[0].title == "Bing 结果"
    assert results[0].provider == "bing"


def test_searxng_parse_and_availability():
    assert SearXNGProvider(base_url="").is_available() is False
    provider = SearXNGProvider(base_url="http://searx.local/")

    async def fake_get(url, params):
        assert url == "http://searx.local/search"
        assert params["format"] == "json"
        return {"results": [{"url": "https://s.example", "title": "S", "content": "摘要"}]}

    provider._get_json = fake_get
    results = asyncio.run(provider.search("q"))
    assert results[0].url == "https://s.example"
    assert results[0].provider == "searxng"


@pytest.mark.parametrize("top_k", [1, 2])
def test_top_k_limit(top_k):
    provider = FakeProvider("tavily", results=[_result(f"https://e.com/{i}") for i in range(5)])
    gateway = WebSearchGateway([provider], top_k=top_k)
    assert len(asyncio.run(gateway.search("q"))) == top_k
