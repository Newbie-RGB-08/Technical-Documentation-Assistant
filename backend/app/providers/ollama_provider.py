"""Ollama 本地 Provider（原生 SDK，PRD 4.8.4）+ 本地模型自动发现（V1.5.x）。

probe() 只调一次 ``client.list()``，同时产出：
1. 服务健康（SERVICE_UNREACHABLE / TIMEOUT / OK）
2. 已安装模型清单（LocalModelInfo，含 parameter_size / family 元数据）
3. 非对话模型过滤（embed/bge/rerank 等关键词名单，config 可覆盖）

不绑定任何具体型号：配置要求的模型未安装不属于错误态，
由网关的自动绑定从已装模型里挑选兜底。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from ollama import AsyncClient

from app.config import get_settings
from app.providers.base import LLMProvider
from app.providers.health import Reason, ProviderStatus, parse_installed_models


def _exclude_keywords() -> tuple[str, ...]:
    try:
        settings = get_settings()
        keywords = [k.strip() for k in settings.local_model_exclude_keywords.split(",") if k.strip()]
        if keywords:
            return tuple(keywords)
    except Exception:  # noqa: BLE001 - 配置读取失败时用默认名单
        pass
    from app.providers.health import DEFAULT_EXCLUDE_KEYWORDS

    return DEFAULT_EXCLUDE_KEYWORDS


class OllamaProvider(LLMProvider):
    name = "ollama"
    provider_type = "local"

    def __init__(self, host: str = "http://localhost:11434", default_model: str = "qwen2.5:7b"):
        self.client = AsyncClient(host=host)
        self.default_model = default_model

    async def chat(
        self, messages: list[dict], model: str | None = None, stream: bool = False, **kwargs
    ):
        target = model or self.default_model
        if stream:
            return self._stream_chat(messages, target)
        response = await self.client.chat(model=target, messages=messages)
        return response["message"]["content"]

    async def _stream_chat(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        stream = await self.client.chat(model=model, messages=messages, stream=True)
        async for chunk in stream:
            content = chunk["message"]["content"]
            if content:
                yield content

    async def is_available(self) -> bool:
        """旧接口（保持原语义）：服务在线即为可用。"""
        try:
            await self.client.list()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def probe(self, timeout: float) -> ProviderStatus:
        """服务级 + 模型级探测；一次 list() 同时产出状态与已装模型清单。"""
        try:
            listing = await asyncio.wait_for(self.client.list(), timeout=timeout)
        except asyncio.TimeoutError:
            return ProviderStatus(
                name=self.name, type="local", available=False,
                reason=Reason.TIMEOUT.value, detail=f"Ollama 在 {timeout}s 内未响应",
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderStatus(
                name=self.name, type="local", available=False,
                reason=Reason.SERVICE_UNREACHABLE.value,
                detail=str(exc) or type(exc).__name__,
            )

        exclude = _exclude_keywords()
        installed = parse_installed_models(listing, exclude_keywords=exclude)
        chatable = [m for m in installed if m.chatable]
        if not chatable:
            return ProviderStatus(
                name=self.name, type="local", available=False,
                reason=Reason.NO_CHAT_MODELS.value, installed_models=installed,
                detail="Ollama 在线，但没有可用于对话的模型（请安装任一对话模型）",
            )
        return ProviderStatus(
            name=self.name, type="local", available=True,
            reason=Reason.OK.value, installed_models=installed,
        )
