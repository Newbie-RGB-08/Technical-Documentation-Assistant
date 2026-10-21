"""P4 测试：SQLite 搜索/抓取缓存（键隔离、TTL、清理）。"""
from datetime import datetime, timedelta

from app.services.web_cache_store import WebCacheStore

RESULTS = [
    {"title": "FastAPI Depends", "url": "https://fastapi.tiangolo.com/x", "snippet": "依赖注入"},
    {"title": "Second", "url": "https://example.com/y", "snippet": "示例"},
]


def _store(tmp_path, now: list[datetime]) -> WebCacheStore:
    return WebCacheStore(
        tmp_path / "web_cache.db",
        search_ttl_hours=1,
        page_ttl_days=1,
        clock=lambda: now[0],
    )


def test_search_cache_roundtrip_and_key_isolation(tmp_path):
    now = [datetime(2026, 3, 1, 12, 0, 0)]
    store = _store(tmp_path, now)

    store.set_search("  FastAPI   Depends ", "tavily", 5, RESULTS)
    assert store.get_search("fastapi depends", "tavily", 5) == RESULTS
    # 不同 provider / top_k 不串键
    assert store.get_search("fastapi depends", "bing", 5) is None
    assert store.get_search("fastapi depends", "tavily", 3) is None
    # 未写入的查询
    assert store.get_search("other", "tavily", 5) is None


def test_search_cache_expires_and_is_removed(tmp_path):
    now = [datetime(2026, 3, 1, 12, 0, 0)]
    store = _store(tmp_path, now)
    store.set_search("q", "tavily", 5, RESULTS)

    now[0] += timedelta(minutes=30)
    assert store.get_search("q", "tavily", 5) == RESULTS

    now[0] += timedelta(hours=2)
    assert store.get_search("q", "tavily", 5) is None
    # 过期行被删除，重新写入后可命中
    store.set_search("q", "tavily", 5, RESULTS)
    assert store.get_search("q", "tavily", 5) == RESULTS


def test_page_cache_roundtrip_and_expiry(tmp_path):
    now = [datetime(2026, 3, 1, 12, 0, 0)]
    store = _store(tmp_path, now)
    page = {
        "url": "https://example.com/doc",
        "title": "Doc",
        "markdown": "# 正文\n\n" + "内容" * 200,
        "provider": "tavily",
        "retrieved_at": "2026-03-01T12:00:00+08:00",
    }
    store.set_page(page)

    cached = store.get_page("https://example.com/doc")
    assert cached is not None
    assert cached["title"] == "Doc"
    assert cached["markdown"] == page["markdown"]
    assert cached["provider"] == "tavily"

    now[0] += timedelta(days=2)
    assert store.get_page("https://example.com/doc") is None


def test_prune_removes_old_rows(tmp_path):
    now = [datetime(2026, 3, 1, 12, 0, 0)]
    store = WebCacheStore(
        tmp_path / "web_cache.db",
        search_ttl_hours=10_000,   # 让 prune 期间不会触发 get_search 自己的过期检查
        page_ttl_days=10_000,
        clock=lambda: now[0],
    )
    store.set_search("q", "tavily", 5, RESULTS)
    store.set_page(
        {
            "url": "https://example.com/old",
            "title": "Old",
            "markdown": "x" * 400,
            "provider": "bing",
            "retrieved_at": "2026-02-01T00:00:00+08:00",
        }
    )

    now[0] += timedelta(days=10)
    store.set_search("fresh", "tavily", 5, RESULTS)
    now[0] += timedelta(days=30)
    pruned = store.prune(days=30)
    assert pruned == {"search": 1, "pages": 1}
    assert store.get_search("fresh", "tavily", 5) == RESULTS


def test_search_cache_ignores_corrupt_payload(tmp_path):
    now = [datetime(2026, 3, 1, 12, 0, 0)]
    store = _store(tmp_path, now)
    store.set_search("q", "tavily", 5, RESULTS)

    with store._conn() as conn:  # noqa: SLF001 - 构造损坏数据
        conn.execute(
            "UPDATE web_search_cache SET results_json = ? WHERE query = ?",
            ("{broken", "q"),
        )
        conn.commit()
    assert store.get_search("q", "tavily", 5) is None
