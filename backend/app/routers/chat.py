"""SSE 流式对话接口（PRD 3.5.2）。

事件：meta / sources / content / confirm / error / done
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.models.schemas import ChatRequest
from app.services.rag_service import chat_flow

router = APIRouter(prefix="/api/chat")

# 同会话串行：避免两次请求互相污染历史与写回队列
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    return _SESSION_LOCKS.setdefault(session_id, asyncio.Lock())


def sse_event(event_type: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


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
