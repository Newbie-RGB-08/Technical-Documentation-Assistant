"""模型清单与健康检查接口（V1.5.x 增加 Provider 状态与联合清单）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.observability import count_api_routes, summarize_route_prefixes

router = APIRouter(prefix="/api")


@router.get("/models")
async def list_models(request: Request, refresh: bool = False):
    """联合模型清单：配置声明 + 本地发现；refresh=True 强制重探。"""
    return await request.app.state.core.gateway.available_models(refresh=refresh)


@router.get("/providers/status")
async def providers_status(request: Request, refresh: bool = False):
    """各 Provider 健康快照 + 本地已装模型清单 + 自动绑定信息（前端横幅消费）。"""
    gateway = request.app.state.core.gateway
    if refresh and getattr(gateway, "probe_enabled", True):
        await gateway.probe_all()
    return gateway.status_report()


@router.get("/health")
async def health(request: Request):
    checks = {"chroma": True, "mysql": True, "sqlite": True}
    try:
        chroma = request.app.state.core.chroma
        # 空集合与尚未创建的集合都应视为健康；任一集合读取失败才降级。
        for source_type in chroma.source_types():
            chroma.count(source_type)
    except Exception:  # noqa: BLE001
        checks["chroma"] = False
    try:
        request.app.state.core.metadata.list_documents()
    except Exception:  # noqa: BLE001
        checks["mysql"] = False
    try:
        request.app.state.core.session_store.get_history("ping", limit=1)
    except Exception:  # noqa: BLE001
        checks["sqlite"] = False
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "checks": checks,
        "started_at": getattr(request.app.state, "started_at", None),
        "route_count": count_api_routes(request.app),
        "route_prefixes": summarize_route_prefixes(request.app),
    }
