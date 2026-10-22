"""LLM Provider 抽象基类（PRD 3.4.2）。"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.providers.health import Reason, ProviderStatus


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
        """健康检查（旧接口，保留不改签名以兼容既有 mock）。"""

    async def probe(self, timeout: float) -> ProviderStatus:
        """结构化探测（V1.5.x）：默认实现委托旧接口 ``is_available()``。

        保留为具体方法而非抽象方法，使既有测试 mock 与未升级子类开箱兼容；
        真实 Provider（ollama / openai_compatible）重写本方法以产出原因码与
        本地模型发现清单。``timeout`` 由网关在探测与调用两层统一施加。
        """
        try:
            available = bool(await asyncio.wait_for(self.is_available(), timeout))
        except asyncio.TimeoutError:
            return ProviderStatus(
                name=self.name, type="cloud", available=False,
                reason=Reason.TIMEOUT.value, detail=f"探测超时（{timeout}s 内无响应）",
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderStatus(
                name=self.name, type="cloud", available=False,
                reason=Reason.ERROR.value, detail=str(exc) or type(exc).__name__,
            )
        return ProviderStatus(
            name=self.name, type="cloud", available=available,
            reason=(Reason.OK.value if available else Reason.ERROR.value),
        )


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class RerankProvider(ABC):
    @abstractmethod
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        ...
