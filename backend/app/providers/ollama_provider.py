"""Ollama 本地 Provider（原生 SDK，PRD 4.8.4）。"""
from __future__ import annotations

from typing import AsyncIterator

from ollama import AsyncClient

from app.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"

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
        try:
            await self.client.list()
            return True
        except Exception:  # noqa: BLE001
            return False
