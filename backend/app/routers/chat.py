"""SSE 流式对话接口（PRD 3.5.2）。

事件：meta / sources / content / confirm / error / done
V1.5.x：全链无可用模型 → E_MODEL_UNAVAILABLE 结构化引导（四象限诊断，不点名型号）。
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.models.schemas import ChatRequest, E_MODEL_UNAVAILABLE
from app.providers.base import AllProvidersFailedError
from app.providers.health import Reason
from app.services.rag_service import chat_flow

router = APIRouter(prefix="/api/chat")

# 同会话串行：避免两次请求互相污染历史与写回队列
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    return _SESSION_LOCKS.setdefault(session_id, asyncio.Lock())


def sse_event(event_type: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def build_model_unavailable_payload(app_state) -> dict:
    """无可用模型的四象限诊断（方案 v2 / D5）。

    文案契约：标题/建议不出现任何具体模型型号（"复述自动绑定的实际选中值"除外，
    其中缀进 tried_auto_models 的机器层面信息，不进入引导文案）。
    """
    gateway = getattr(app_state, "gateway", None)
    report = None
    if gateway is not None and hasattr(gateway, "status_report"):
        try:
            report = gateway.status_report()
        except Exception:  # noqa: BLE001 - 诊断自身失败不影响主错误
            report = None
    providers = list((report or {}).get("providers") or [])
    bindings = dict((report or {}).get("bindings") or {})
    gate_enabled = bool((report or {}).get("enabled", True))

    local_rows = [p for p in providers if p.get("type") == "local"]
    cloud_rows = [p for p in providers if p.get("type") == "cloud"]
    local_ok = any(p.get("available") for p in local_rows)
    cloud_ok = any(p.get("available") for p in cloud_rows)
    no_chat_models = any(
        p.get("reason") == Reason.NO_CHAT_MODELS.value for p in local_rows
    )
    auth_failed = any(
        p.get("reason") == Reason.AUTH_FAILED.value for p in cloud_rows
    )

    tried_auto_models = [
        {"provider": binding.get("provider"), "model": binding.get("model")}
        for binding in bindings.values()
        if isinstance(binding, dict) and binding.get("provider")
    ]

    if auth_failed:
        title = "云端凭据无效"
        suggestion = "核对 backend/.env 中对应的 API Key；或修复本地 Ollama 后重试。"
    elif no_chat_models and not cloud_ok:
        title = "Ollama 在线，但未安装任何对话模型"
        suggestion = (
            "执行 ollama pull <模型名> 安装任一对话模型；"
            "或在 backend/.env 配置在线模型 Key 后重启后端。"
        )
    elif not local_ok and not cloud_ok:
        configured_cloud = bool(cloud_rows)
        if configured_cloud and gate_enabled:
            title = "无可用模型，请检查网络与服务状态"
            suggestion = "稍后重试；若持续失败请查看后端日志。"
        else:
            title = "本地 Ollama 不可用，在线模型未配置"
            suggestion = (
                "启动 Ollama 服务并安装任一对话模型；"
                "或在 backend/.env 配置 DEEPSEEK_API_KEY"
                "（或 QWEN_API_KEY / OPENAI_API_KEY）后重启后端。"
            )
    elif not local_ok and cloud_ok:
        title = "本地 Ollama 不可用，在线模型未配置"
        suggestion = "启动 Ollama 服务并安装任一对话模型，或稍后重试。"
    else:
        title = "无可用模型，请稍后重试"
        suggestion = "重试；若持续失败请查看后端日志。"

    return {
        "code": E_MODEL_UNAVAILABLE,
        "message": title,
        "suggestion": suggestion,
        "diagnosis": [
            {"provider": p.get("name"), "type": p.get("type"), "reason": p.get("reason")}
            for p in sorted(providers, key=lambda x: str(x.get("name")))
        ],
        "tried_auto_models": tried_auto_models,
    }


@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request):
    app_state = request.app.state.core
    session_id = req.session_id or str(uuid.uuid4())

    async def event_generator():
        lock = _session_lock(session_id)
        if lock.locked():
            yield sse_event(
                "error",
                {
                    "code": "E_SESSION_BUSY",
                    "message": "该会话正在处理上一条提问。",
                    "suggestion": "请等待当前回答完成后再提问。",
                },
            )
            yield sse_event(
                "done",
                {
                    "session_id": session_id,
                    "answer_status": "error",
                    "interrupted": True,
                },
            )
            return

        async with lock:
            try:
                async for event_type, data in chat_flow(
                    app_state,
                    question=req.question,
                    session_id=session_id,
                    top_k=req.top_k,
                    model_id=req.model_id,
                ):
                    yield sse_event(event_type, data)
            except asyncio.CancelledError:
                raise
            except AllProvidersFailedError:
                # V1.5.x：全链无可用模型 → 结构化四象限引导（不再暴露原始报错串）
                yield sse_event("error", build_model_unavailable_payload(app_state))
                yield sse_event(
                    "done",
                    {"session_id": session_id, "answer_status": "error", "interrupted": False},
                )
                return
            except Exception as exc:  # noqa: BLE001 - 顶层异常转 error 事件
                yield sse_event(
                    "error",
                    {
                        "code": "E_INTERNAL",
                        "message": str(exc) or "服务内部错误",
                        "suggestion": "请重试；若持续失败请查看后端日志。",
                    },
                )
                yield sse_event(
                    "done",
                    {
                        "session_id": session_id,
                        "answer_status": "error",
                        "interrupted": False,
                    },
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history/{session_id}")
async def chat_history(session_id: str, request: Request, limit: int = 50):
    return request.app.state.core.session_store.get_history(session_id, limit=limit)


@router.post("/feedback")
async def record_feedback(request: Request, payload: dict):
    """"记录此问题"占位（原转人工的简化）。"""
    session_id = payload.get("session_id", "")
    question = payload.get("question", "")
    app_state = request.app.state.core
    app_state.session_store.add_feedback(session_id, question, kind="manual_review")
    return {"ok": True, "message": "问题已记录，后续可在 feedback 表中回顾。"}
