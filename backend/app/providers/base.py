"""LLM Provider 抽象基类（PRD 3.4.2）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


class AllProvidersFailedError(Exception):
    """路由链上所有 Provider 均不可用。"""


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def chat(
        self, messages: list[dict], model: str | None = None, stream: bool = False, **kwargs
    ) -> str | AsyncIterator[str]:
        """对话补全；stream=True 时返回异步迭代器。"""

    @abstractmethod
    async def is_available(self) -> bool:
        """健康检查。"""


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class RerankProvider(ABC):
    @abstractmethod
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        ...
