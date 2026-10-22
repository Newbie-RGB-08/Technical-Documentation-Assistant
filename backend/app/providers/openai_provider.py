"""OpenAI 兼容 Provider：DeepSeek / Qwen（百炼）/ OpenAI 共用（PRD 4.8.4）。"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from openai import AsyncOpenAI

from app.providers.base import LLMProvider
from app.providers.health import Reason, ProviderStatus


class OpenAICompatibleProvider(LLMProvider):
    provider_type = "cloud"

    def __init__(self, name: str, base_url: str, api_key: str, default_model: str):
        self.name = name
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.default_model = default_model

    async def chat(
        self, messages: list[dict], model: str | None = None, stream: bool = False, **kwargs
    ):
        target = model or self.default_model
        if stream:
            return self._stream_chat(messages, target)
        response = await self.client.chat.completions.create(
            model=target, messages=messages, **kwargs
        )
        return response.choices[0].message.content

    async def _stream_chat(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=model, messages=messages, stream=True, **{}
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def is_available(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def probe(self, timeout: float) -> ProviderStatus:
        """结构化探测：可达性 + 凭据有效性（401/403 → auth_failed）。"""
        try:
            await asyncio.wait_for(self.client.models.list(), timeout=timeout)
        except asyncio.TimeoutError:
            return ProviderStatus(
                name=self.name, type="cloud", available=False,
                reason=Reason.TIMEOUT.value, detail=f"云端服务在 {timeout}s 内未响应",
            )
        except Exception as exc:  # noqa: BLE001
            status_code = getattr(exc, "status_code", None)
            if status_code in (401, 403) or type(exc).__name__.lower().find("auth") >= 0:
                reason = Reason.AUTH_FAILED.value
                detail = "云端凭据无效（请核对 .env 中对应的 API Key）"
            else:
                reason = Reason.SERVICE_UNREACHABLE.value
                detail = str(exc) or type(exc).__name__
            return ProviderStatus(
                name=self.name, type="cloud", available=False, reason=reason, detail=detail,
            )
        return ProviderStatus(name=self.name, type="cloud", available=True, reason=Reason.OK.value)
