"""P2：解析器注册表、懒加载与健康缓存。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.parsers.base import DocumentParser, ParsedDocument
from app.parsers.registry import (
    PARSERS,
    ParserRegistry,
    ParserUnavailableError,
    UnknownParserError,
)


class CountingParser(DocumentParser):
    name = "counting"
    supported_extensions = (".txt",)

    def __init__(self):
        self.health_calls = 0
        self.available_calls = 0

    def available(self) -> bool:
        self.available_calls += 1
        return True

    def health(self) -> dict:
        self.health_calls += 1
        return {"available": True, "status": "ok", "formats": [".txt"]}

    def parse(self, src: Path, out_dir: Path, params: dict) -> ParsedDocument:
        return ParsedDocument(markdown="")


def test_capability_table_and_unknown_engine():
    assert "native" in PARSERS and "rapidocr" in PARSERS
    registry = ParserRegistry(ttl_seconds=30)
    with pytest.raises(UnknownParserError, match="未知解析引擎"):
        registry.get_parser("nope")


def test_native_is_lazy_and_available():
    registry = ParserRegistry(ttl_seconds=30)
    parser = registry.get_parser("native")
    assert parser.name == "native" and parser.available()
    # 实例复用
    assert registry.get_parser("native") is parser
    health = registry.health("native")
    assert health["available"] is True and health["status"] == "ok"
    assert ".pdf" in health["formats"]


def test_health_cache_ttl_and_refresh():
    registry = ParserRegistry(ttl_seconds=30)
    parser = CountingParser()
    PARSERS["counting"] = "unused:CountingParser"
    try:
        registry._instances["counting"] = parser
        first = registry.health("counting")
        second = registry.health("counting")
        assert first["available"] and second["available"]
        assert parser.health_calls == 1
        registry.health("counting", refresh=True)
        assert parser.health_calls == 2

        # TTL 过期后重新探测
        registry.ttl_seconds = 0
        registry.health("counting")
        assert parser.health_calls == 3
    finally:
        PARSERS.pop("counting", None)


def test_health_failure_is_reported_not_raised(monkeypatch):
    class BrokenParser(CountingParser):
        def health(self):
            raise RuntimeError("engine down")

    registry = ParserRegistry(ttl_seconds=30)
    PARSERS["broken"] = "unused:BrokenParser"
    try:
        registry._instances["broken"] = BrokenParser()
        health = registry.health("broken")
        assert health["available"] is False
        assert health["status"] == "unavailable"
        assert "engine down" in health["detail"]
    finally:
        PARSERS.pop("broken", None)


def test_missing_dependency_does_not_break_registry():
    registry = ParserRegistry(ttl_seconds=30)
    PARSERS["missing_dep"] = "module_that_does_not_exist_xyz:Parser"
    try:
        with pytest.raises(ParserUnavailableError, match="不可用"):
            registry.get_parser("missing_dep")
        assert registry.available("missing_dep") is False
        health = registry.health("missing_dep")
        assert health["available"] is False
    finally:
        PARSERS.pop("missing_dep", None)


def test_registry_import_does_not_load_heavy_ocr_dependencies():
    """模块顶层不能 import rapidocr/onnxruntime，否则无 OCR 环境无法启动。"""
    backend = Path(__file__).resolve().parents[1]
    code = (
        "import sys; import app.parsers.registry; "
        "assert 'rapidocr' not in sys.modules; "
        "assert 'onnxruntime' not in sys.modules; print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(backend),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
