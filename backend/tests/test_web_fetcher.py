"""P3：抓取安全约束、大小/超时/并发、失败隔离与正文优先。"""
import asyncio

import httpx
import pytest

from app.services.web_fetcher import fetch_pages, is_safe_url
from app.services.web_search import WebResult

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def _run(coro):
    return asyncio.run(coro)


def test_is_safe_url_allows_public_http():
    assert is_safe_url("https://example.com/doc", resolver=PUBLIC) is True
    assert is_safe_url("http://example.com/doc", resolver=PUBLIC) is True


def test_is_safe_url_rejects_protocols_and_private_targets():
    assert is_safe_url("ftp://example.com/x", resolver=PUBLIC) is False
    assert is_safe_url("javascript:alert(1)", resolver=PUBLIC) is False
    assert is_safe_url("http://127.0.0.1/admin", resolver=PUBLIC) is False
    assert is_safe_url("http://localhost:8000/x", resolver=PUBLIC) is False
    assert is_safe_url("http://10.0.0.5/x", resolver=PUBLIC) is False
    assert is_safe_url("http://192.168.1.2/x", resolver=PUBLIC) is False
    assert is_safe_url("http://169.254.169.254/latest/meta-data", resolver=PUBLIC) is False
    assert is_safe_url("http://[::1]/x", resolver=PUBLIC) is False
    assert is_safe_url("http://metadata.google.internal/x", resolver=PUBLIC) is False


def test_is_safe_url_rejects_private_dns_resolution():
    assert is_safe_url("https://evil.example", resolver=lambda host: ["10.0.0.8"]) is False
    assert is_safe_url("https://broken.example", resolver=lambda host: []) is False

    def raises(host):
        raise OSError("dns")

    assert is_safe_url("https://broken.example", resolver=raises) is False


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


_HTML = "<html><head><title>示例页</title></head><body><article>" + ("正文内容。" * 100) + "</article></body></html>"


def test_fetch_pages_cleans_html():
    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=_HTML)

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [WebResult(url="https://example.com/a", title="搜索标题", provider="fake")],
                client=client,
                resolver=PUBLIC,
                min_interval=0,
            )

    pages = _run(run())
    assert len(pages) == 1
    assert pages[0].title == "示例页"
    assert "正文内容" in pages[0].markdown
    assert pages[0].retrieved_at


def test_fetch_pages_skips_non_html_and_http_errors():
    async def handler(request):
        if request.url.path == "/pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
        if request.url.path == "/404":
            return httpx.Response(404, headers={"content-type": "text/html"}, text=_HTML)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_HTML)

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [
                    WebResult(url="https://example.com/pdf"),
                    WebResult(url="https://example.com/404"),
                    WebResult(url="https://example.com/ok"),
                ],
                client=client,
                resolver=PUBLIC,
                min_interval=0,
            )

    pages = _run(run())
    assert [p.url for p in pages] == ["https://example.com/ok"]


def test_fetch_pages_rejects_unsafe_and_redirect_to_private():
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/secret"}
        )

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [
                    WebResult(url="http://127.0.0.1/admin"),
                    WebResult(url="https://example.com/redirect"),
                ],
                client=client,
                resolver=PUBLIC,
                min_interval=0,
            )

    pages = _run(run())
    assert pages == []
    assert calls == ["https://example.com/redirect"]  # 重定向目标未发起请求


def test_fetch_pages_skips_oversized_body():
    big = "<html><body><article>" + ("x" * 5000) + "</article></body></html>"

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=big)

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [WebResult(url="https://example.com/big")],
                client=client,
                resolver=PUBLIC,
                max_bytes=1000,
                min_interval=0,
            )

    assert _run(run()) == []


def test_fetch_pages_prefers_provider_content_without_http():
    async def handler(request):  # pragma: no cover - 不应被调用
        raise AssertionError("不应发起 HTTP 请求")

    content = "# 标题\n\n" + ("Provider 返回的完整正文。" * 40)

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [WebResult(url="https://example.com/a", title="T", content=content)],
                client=client,
                resolver=PUBLIC,
                min_interval=0,
            )

    pages = _run(run())
    assert len(pages) == 1
    assert pages[0].markdown.startswith("# 标题")


def test_fetch_pages_concurrency_capped():
    active = 0
    peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_HTML)

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [WebResult(url=f"https://example.com/{i}") for i in range(6)],
                client=client,
                resolver=PUBLIC,
                top_n=6,
                concurrency=3,
                per_page_timeout=2,
                total_timeout=5,
                min_interval=0,
            )

    pages = _run(run())
    assert len(pages) == 6
    assert peak <= 3


def test_fetch_pages_timeout_is_isolated():
    async def handler(request):
        if request.url.path == "/slow":
            await asyncio.sleep(0.5)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_HTML)

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [WebResult(url="https://example.com/slow"), WebResult(url="https://example.com/fast")],
                client=client,
                resolver=PUBLIC,
                per_page_timeout=0.1,
                total_timeout=2,
                min_interval=0,
            )

    pages = _run(run())
    assert [p.url for p in pages] == ["https://example.com/fast"]


@pytest.mark.parametrize("top_n", [1, 2])
def test_fetch_pages_respects_top_n(top_n):
    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_HTML)

    async def run():
        async with _client(handler) as client:
            return await fetch_pages(
                [WebResult(url=f"https://example.com/{i}") for i in range(5)],
                client=client,
                resolver=PUBLIC,
                top_n=top_n,
                min_interval=0,
            )

    assert len(_run(run())) == top_n
