"""OpenAI 兼容 Provider：DeepSeek / Qwen（百炼）/ OpenAI 共用（PRD 4.8.4）。"""
from __future__ import annotations

from typing import AsyncIterator

from openai import AsyncOpenAI

from app.providers.base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
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
