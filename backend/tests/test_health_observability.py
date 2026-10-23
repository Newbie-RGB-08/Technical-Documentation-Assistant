"""健康检查可观测性：启动时间、路由前缀清单、路由条数。

背景：8000 端口残留旧进程（未加载 /api/uploads）时前端报 404，仅看 `/api/health`
无法判断服务是否为最新代码。这里固化新的契约，防止回归。
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import count_api_routes, summarize_route_prefixes
from app.routers import health


def _stub_core():
    chroma = SimpleNamespace(source_types=lambda: [], count=lambda name: 0)
    metadata = SimpleNamespace(list_documents=lambda: [])
    session_store = SimpleNamespace(get_history=lambda sid, limit=1: [])
    return SimpleNamespace(chroma=chroma, metadata=metadata, session_store=session_store)


def _health_app(started_at: str | None = "2026-10-23T08:00:00+00:00") -> FastAPI:
    app = FastAPI()
    app.state.core = _stub_core()
    if started_at is not None:
        app.state.started_at = started_at
    app.include_router(health.router)
    return app


# ---- summarize_route_prefixes / count_api_routes ----

def test_summarize_folds_path_params_and_dedups():
    app = FastAPI()

    @app.get("/api/uploads")
    def list_uploads():
        return {}

    @app.get("/api/uploads/{upload_id}/markdown")
    def markdown(upload_id: str):
        return {}

    @app.get("/api/uploads/engines")
    def engines():
        return {}

    @app.get("/api/chat/stream")
    def stream():
        return {}

    assert summarize_route_prefixes(app) == ["/api/chat", "/api/uploads"]
    assert count_api_routes(app) == 4


def test_summarize_ignores_non_api_routes():
    app = FastAPI()

    @app.get("/docs")
    def docs():
        return {}

    @app.get("/api/health")
    def healthz():
        return {}

    assert summarize_route_prefixes(app) == ["/api/health"]
    assert count_api_routes(app) == 1


def test_summarize_empty_app():
    app = FastAPI()
    assert summarize_route_prefixes(app) == []
    assert count_api_routes(app) == 0


# ---- /api/health 新字段 ----

def test_health_reports_started_at_and_routes():
    app = _health_app()
    body = TestClient(app).get("/api/health").json()

    assert body["status"] == "ok"
    assert body["started_at"] == "2026-10-23T08:00:00+00:00"
    assert body["route_count"] >= 1
    assert "/api/health" in body["route_prefixes"]


def test_health_started_at_absent_is_none():
    app = _health_app(started_at=None)
    body = TestClient(app).get("/api/health").json()
    assert body["started_at"] is None
    assert body["route_count"] >= 1


def test_health_degraded_still_reports_routes():
    app = FastAPI()
    app.state.core = SimpleNamespace(
        chroma=SimpleNamespace(source_types=lambda: ["tech_doc"], count=lambda n: (_ for _ in ()).throw(RuntimeError("boom"))),
        metadata=SimpleNamespace(list_documents=lambda: []),
        session_store=SimpleNamespace(get_history=lambda sid, limit=1: []),
    )
    app.state.started_at = "2026-10-23T08:00:00+00:00"
    app.include_router(health.router)

    body = TestClient(app).get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["checks"]["chroma"] is False
    assert body["started_at"] == "2026-10-23T08:00:00+00:00"


# ---- 主应用路由注册回归守卫 ----

def test_main_app_registers_uploads_router():
    """原始 404 的根因是旧进程缺少上传路由；此用例锁定主应用必然注册它。"""
    from app.main import app

    prefixes = summarize_route_prefixes(app)
    assert "/api/uploads" in prefixes
    assert "/api/chat" in prefixes
    assert count_api_routes(app) >= 20


def test_startup_log_lists_route_prefixes(caplog):
    """阶段3：启动日志需打印路由前缀，一眼可判断进程是否加载新代码。"""
    import logging

    from app.main import _log_registered_routes, app

    with caplog.at_level(logging.INFO, logger="app.main"):
        _log_registered_routes(app)

    message = caplog.text
    assert "已注册 API 路由" in message
    assert "/api/uploads" in message
    assert "/api/chat" in message

