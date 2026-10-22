"""Pydantic 请求/响应模型与 SSE 事件协议（PRD 3.5.2）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    directory: str = "./data/notes"
    source_type: str = "personal_note"
    force_reingest: bool = False


class IngestResponse(BaseModel):
    task_id: str
    status: str
    file_count: int


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    model_id: str | None = None
    web_mode: str | None = None  # auto / off / force
    answer_mode: str | None = None  # hybrid / strict / stream


class SourceItem(BaseModel):
    index: int
    file_path: str
    heading_path: str
    excerpt: str
    score: float
    # V1.4 可选字段：老会话与旧 payload 无需迁移
    title: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    origin: str | None = None
    retrieved_at: str | None = None
    trust_level: str | None = None
    is_hybrid: bool | None = None


class ModelOption(BaseModel):
    id: str
    name: str
    provider: str
    type: str
    available: bool
    # V1.5.x 可选字段（联合清单）：来源/reason/发现元数据
    source: str | None = None        # "config" | "discovered"
    reason: str | None = None
    parameter_size: float | None = None
    family: str | None = None
    missing_models: list[str] | None = None


# 结构化错误码（V1.5.x / 方案 v2 D5）：无任何可用模型时的引导性错误
E_MODEL_UNAVAILABLE = "E_MODEL_UNAVAILABLE"

# SSE 事件类型（PRD 3.5.2 + V1.4 status）：meta / status / sources / content / confirm / error / done
SSE_EVENTS = ("meta", "status", "sources", "content", "confirm", "error", "done")
