"""解析引擎注册表与懒加载工厂（PRD FR-3）。"""
from __future__ import annotations

import importlib
import threading
import time
from typing import Any

from app.parsers.base import DocumentParser, ParserError

# 只保存 module:class 字符串，模块顶层绝不 import rapidocr/onnxruntime。
PARSERS: dict[str, str] = {
    "native": "app.parsers.native:NativeParser",
    "rapidocr": "app.parsers.rapidocr_engine:RapidOCRParser",
    "mineru": "app.parsers.mineru:MinerUParser",
    "paddlex": "app.parsers.paddlex:PaddleXParser",
}


class ParserUnavailableError(ParserError):
    pass


class UnknownParserError(ValueError):
    pass


class ParserRegistry:
    def __init__(self, ttl_seconds: float = 30.0):
        self.ttl_seconds = ttl_seconds
        self._instances: dict[str, DocumentParser] = {}
        self._health: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get_parser(self, name: str) -> DocumentParser:
        if name not in PARSERS:
            raise UnknownParserError(
                f"未知解析引擎: {name}；可用: {', '.join(PARSERS)}"
            )
        with self._lock:
            cached = self._instances.get(name)
        if cached is not None:
            return cached
        module_name, _, class_name = PARSERS[name].partition(":")
        try:
            module = importlib.import_module(module_name)
            parser_class = getattr(module, class_name)
            parser = parser_class()
        except Exception as exc:  # noqa: BLE001 - 依赖缺失不影响服务启动
            raise ParserUnavailableError(
                f"解析引擎 {name} 不可用: {type(exc).__name__}: {exc}"
            ) from exc
        with self._lock:
            self._instances[name] = parser
        return parser

    def available(self, name: str) -> bool:
        try:
            parser = self.get_parser(name)
            return bool(parser.available())
        except Exception:  # noqa: BLE001
            return False

    def health(self, name: str, refresh: bool = False) -> dict:
        now = time.monotonic()
        with self._lock:
            cached = self._health.get(name)
        if not refresh and cached and now - cached[0] < self.ttl_seconds:
            return dict(cached[1])
        try:
            parser = self.get_parser(name)
            payload = parser.health()
        except Exception as exc:  # noqa: BLE001
            payload = {
                "name": name,
                "available": False,
                "status": "unavailable",
                "detail": f"{type(exc).__name__}: {exc}",
                "latency_ms": None,
                "checked_at": time.time(),
                "formats": [],
            }
        payload = {"name": name, **payload}
        with self._lock:
            self._health[name] = (now, dict(payload))
        return payload

    def all_health(self, refresh: bool = False) -> list[dict]:
        return [self.health(name, refresh=refresh) for name in PARSERS]

    def capabilities(self) -> list[dict]:
        result = []
        for name in PARSERS:
            health = self.health(name)
            result.append(
                {
                    "name": name,
                    "formats": health.get("formats") or [],
                    "available": bool(health.get("available")),
                    "health": health,
                }
            )
        return result


_registry: ParserRegistry | None = None


def get_registry(ttl_seconds: float | None = None) -> ParserRegistry:
    global _registry
    if _registry is None or ttl_seconds is not None:
        from app.config import get_settings

        ttl = ttl_seconds
        if ttl is None:
            ttl = get_settings().upload_engine_health_ttl_seconds
        _registry = ParserRegistry(ttl_seconds=ttl)
    return _registry


def get_parser(name: str) -> DocumentParser:
    return get_registry().get_parser(name)


def parser_health(name: str, refresh: bool = False) -> dict:
    return get_registry().health(name, refresh=refresh)


def all_parser_health(refresh: bool = False) -> list[dict]:
    return get_registry().all_health(refresh=refresh)
