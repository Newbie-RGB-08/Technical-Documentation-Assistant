"""启动可观测性：汇总已注册的 API 路由前缀。

用途：`/api/health` 与启动日志都能一眼看出"当前进程是否加载了最新路由"，
避免再次出现"代码已更新但端口上跑着旧进程 → 404"的排查成本。

兼容性说明：FastAPI 0.159 起 `app.include_router()` 生成的是 `_IncludedRouter`
包装节点（`route.path` 为 None），真实路径在其 `original_router.routes` 内，
因此这里递归展开。
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from fastapi import FastAPI

API_PREFIX = "/api"


def _iter_api_paths(routes: Iterable[object]) -> Iterator[str]:
    for route in routes:
        nested = getattr(route, "original_router", None) or getattr(route, "router", None)
        if nested is not None:
            yield from _iter_api_paths(getattr(nested, "routes", []))
            continue
        path = getattr(route, "path", None)
        if isinstance(path, str) and path.startswith(API_PREFIX):
            yield path


def summarize_route_prefixes(app: FastAPI) -> list[str]:
    """把具体路由折叠成二级前缀，去重排序。

    例：`/api/uploads/{upload_id}/markdown` → `/api/uploads`。
    """
    prefixes: set[str] = set()
    for path in _iter_api_paths(getattr(app, "routes", [])):
        segments = [part for part in path.strip("/").split("/") if part]
        if len(segments) >= 2:
            prefixes.add(f"/{segments[0]}/{segments[1]}")
    return sorted(prefixes)


def count_api_routes(app: FastAPI) -> int:
    """已注册的 /api 路由条数（含参数化路径，不含 /docs 等）。"""
    return sum(1 for _ in _iter_api_paths(getattr(app, "routes", [])))


__all__ = ["summarize_route_prefixes", "count_api_routes"]
