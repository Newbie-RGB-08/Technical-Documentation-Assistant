"""FastAPI 应用入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import chat, documents, files, health
from app.services.llm_gateway import LLMGateway
from app.state import AppState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.core = AppState.build()
    await _startup_model_probe(app.state.core.gateway)
    yield


async def _startup_model_probe(gateway: LLMGateway) -> None:
    """启动自检（V1.5.x / 方案 v2 D4）：探测 Provider 健康与本地已装模型并自动绑定兜底。

    绝不阻断启动：探测/绑定任何异常都只记日志，等首轮请求现场重探。
    """
    try:
        await gateway.probe_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动模型探测失败（后续请求将现场重检）: %s", exc)
        return
    try:
        gateway.bind_defaults()
        gateway.choose_default()
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动模型绑定失败（按配置链降级）: %s", exc)
        return
    _log_startup_models(gateway)


def _log_startup_models(gateway: LLMGateway) -> None:
    """三态启动日志（方案 v2 / D6 契约）。不出现具体型号的引导，只复述绑定结果。"""
    if not gateway.probe_enabled:
        logger.info("模型健康探测已关闭（provider_probe_enabled=false）→ 按配置链逐次降级")
        return

    statuses = gateway._health
    cloud_ok = [n for n, st in statuses.items() if st.type == "cloud" and st.available]
    cloud_note = f"; 已配置在线模型: {', '.join(cloud_ok)} → 将使用在线模型" if cloud_ok else ""
    local_bad = {n: st for n, st in statuses.items() if st.type == "local" and not st.available}
    bound = {tt: b for tt, b in gateway.auto_bindings.items() if b is not None}

    if bound:
        for task_type, binding in bound.items():
            logger.info(
                "配置模型未安装，已自动选用本地模型: %s→%s/%s（%s）",
                task_type, binding.provider, binding.model, binding.reason,
            )
            logger.info("本地模型就绪: %s 个候选可于前端下拉框切换", len(gateway.installed_chat_models()))
        return

    if len(local_bad) == len(statuses) and statuses and not cloud_ok:
        diagnosis = "; ".join(f"{n}={st.reason}" for n, st in sorted(statuses.items()))
        logger.error(
            "无可用模型。诊断: %s → 请启动 Ollama（并安装任一对话模型）"
            "或在 .env 配置在线模型 Key 后重启后端", diagnosis,
        )
        return

    for name, st in local_bad.items():
        if st.reason == "no_chat_models":
            logger.warning("Ollama 在线但未安装对话模型 → 执行 ollama pull <模型名>%s", cloud_note)
        elif st.reason == "service_unreachable":
            logger.info("本地 Ollama 不可达（服务未启动或端口无响应）%s", cloud_note)
        else:
            logger.info("本地 Ollama 健康未通过（%s）%s", st.reason, cloud_note)
    if not local_bad:
        logger.info("本地模型在线，共发现 %s 个候选", len(gateway.installed_chat_models()))


app = FastAPI(title="PTDRA - 个人技术文档研究助手", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(health.router)
app.include_router(files.router)
