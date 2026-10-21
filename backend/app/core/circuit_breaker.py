"""Provider / 域名熔断器（PRD E.8.3）。

连续失败达到阈值后熔断冷却一段时间；冷却结束自动半开放行，
成功一次即清零。键可以是 provider 名或域名。
"""
from __future__ import annotations

import time

from app.config import get_settings


class CircuitBreaker:
    def __init__(
        self,
        threshold: int | None = None,
        cooldown_seconds: float | None = None,
        clock=time.monotonic,
    ):
        settings = get_settings()
        self.threshold = int(
            threshold if threshold is not None else settings.llm_circuit_breaker_threshold
        )
        self.cooldown_seconds = float(
            cooldown_seconds
            if cooldown_seconds is not None
            else settings.circuit_breaker_cooldown_seconds
        )
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, key: str) -> bool:
        opened_at = self._opened_at.get(key)
        if opened_at is None:
            return False
        if self._clock() - opened_at >= self.cooldown_seconds:
            # 冷却结束：半开，允许一次尝试
            self._opened_at.pop(key, None)
            self._failures[key] = 0
            return False
        return True

    def allow(self, key: str) -> bool:
        return not self.is_open(key)

    def record_failure(self, key: str) -> None:
        if self.is_open(key):
            return
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if failures >= self.threshold:
            self._opened_at[key] = self._clock()

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)

    def failure_count(self, key: str) -> int:
        return self._failures.get(key, 0)

    def reset(self) -> None:
        self._failures.clear()
        self._opened_at.clear()
