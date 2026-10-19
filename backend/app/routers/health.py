"""模型清单与健康检查接口。"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/models")
async def list_models(request: Request):
    return await request.app.state.core.gateway.available_models()


@router.get("/health")
async def health(request: Request):
    checks = {"chroma": True, "mysql": True, "sqlite": True}
    try:
        request.app.state.core.chroma.count("personal_note")
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
    return {"status": "ok" if all(checks.values()) else "degraded", "checks": checks}
