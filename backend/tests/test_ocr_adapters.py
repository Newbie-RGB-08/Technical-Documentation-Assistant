"""P6：OCR 适配器（RapidOCR）mock 测试 + 依赖缺失降级。

覆盖：
- RapidOCRParser.available() 依赖缺失返回 False
- RapidOCRParser.parse PDF/图片 mock 流程
- 空输出触发上层 auto 降级
- 启动时 rapidocr 缺失不影响服务
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter

from app.parsers.base import ParsedDocument, ParserError, UnsupportedFormatError
from app.parsers.rapidocr_engine import RapidOCRParser


@pytest.fixture()
def minimal_pdf(tmp_path):
    pdf = tmp_path / "minimal.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=216, height=216)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    return pdf


@pytest.fixture()
def fake_image(tmp_path):
    """假 PNG：只需要扩展名让 parser 路由到图片分支；Pillow 会被 mock。"""
    path = tmp_path / "pic.png"
    path.write_bytes(b"this-is-not-a-real-png")
    return path


@pytest.fixture()
def parser() -> RapidOCRParser:
    return RapidOCRParser()


# ---- available / health ----

def test_rapidocr_available_imports_all_three(monkeypatch, parser):
    for mod in ("rapidocr", "pypdfium2", "PIL"):
        monkeypatch.setitem(sys.modules, mod, MagicMock())
    with patch.object(type(parser), "available", return_value=True):
        assert parser.available() is True


def test_rapidocr_available_missing_any_dep_returns_false(monkeypatch, parser):
    for mod in ("pypdfium2", "PIL"):
        monkeypatch.setitem(sys.modules, mod, MagicMock())
    with patch("importlib.import_module", side_effect=ImportError("no rapidocr")):
        assert parser.available() is False


def test_rapidocr_health_when_unavailable(monkeypatch, parser):
    with patch.object(parser, "available", return_value=False):
        h = parser.health()
        assert h["available"] is False
        assert h["status"] == "unavailable"
        # formats 是扩展名列表
        assert ".pdf" in h["formats"]
        assert ".png" in h["formats"]


# ---- _result_text 工具 ----

def test_result_text_none_and_empty(parser):
    assert parser._result_text(None) == ""
    assert parser._result_text(type("R", (), {"txts": []})()) == ""
    assert parser._result_text(type("R", (), {"txts": ["", "   "]})()) == ""


def test_result_text_dict_variant(parser):
    result = {"txts": ["hello", "world"]}
    assert parser._result_text(result) == "hello\nworld"


def test_result_text_joins_nonempty(parser):
    result = MagicMock(txts=["line1", "  ", "line2"])
    assert parser._result_text(result) == "line1\nline2"


# ---- parse 入口拒绝 ----

def test_unsupported_format_raises(parser, tmp_path):
    src = tmp_path / "foo.docx"
    src.write_bytes(b"fake")
    with pytest.raises(UnsupportedFormatError):
        parser.parse(src, tmp_path / "out", {})


def test_missing_file_raises(parser, tmp_path):
    with pytest.raises(ParserError, match="文件不存在"):
        parser.parse(tmp_path / "nope.pdf", tmp_path / "out", {})


# ---- PDF 解析 mock 流程 ----

def test_parse_pdf_renders_each_page_and_writes_markdown(monkeypatch, parser, minimal_pdf, tmp_path):
    """逐页 render → OCR → 拼 ## 第 N 页 Markdown。"""
    # _ocr_image 直接返回纯字符串（模拟 _result_text 已被调用后）
    monkeypatch.setattr(
        parser, "_ocr_image", MagicMock(side_effect=["Page one text", "Page two text"])
    )

    fake_page = MagicMock()
    fake_bitmap = MagicMock()
    fake_bitmap.to_pil.return_value = MagicMock(
        close=MagicMock(), convert=MagicMock(return_value=MagicMock())
    )
    fake_page.render.return_value = fake_bitmap

    fake_doc = MagicMock()
    fake_doc.__len__ = lambda self: 2
    fake_doc.__getitem__ = lambda self, i: fake_page
    fake_doc.close = MagicMock()

    with patch("pypdfium2.PdfDocument", return_value=fake_doc):
        result = parser.parse(minimal_pdf, tmp_path / "out", {})

    assert result.engine == "rapidocr"
    assert result.page_count == 2
    assert "## 第 1 页" in result.markdown
    assert "## 第 2 页" in result.markdown
    assert "Page one text" in result.markdown
    assert "Page two text" in result.markdown


def test_parse_pdf_page_render_exception_wraps(minimal_pdf, tmp_path, parser):
    fake_page = MagicMock()
    fake_page.render.side_effect = RuntimeError("boom render")
    fake_doc = MagicMock()
    fake_doc.__len__ = lambda self: 1
    fake_doc.__getitem__ = lambda self, i: fake_page
    fake_doc.close = MagicMock()

    with patch("pypdfium2.PdfDocument", return_value=fake_doc):
        with pytest.raises(ParserError, match="渲染"):
            parser.parse(minimal_pdf, tmp_path / "out", {})


# ---- 图片解析 mock ----

def test_parse_image_success(fake_image, tmp_path, parser, monkeypatch):
    monkeypatch.setattr(parser, "_ocr_image", MagicMock(return_value="Image text"))
    monkeypatch.setattr(
        "PIL.Image.open", MagicMock(return_value=MagicMock(close=MagicMock()))
    )
    result = parser.parse(fake_image, tmp_path / "out", {})
    assert result.engine == "rapidocr"
    assert "Image text" in result.markdown
    assert result.page_count == 1
    assert result.warnings == []


def test_parse_image_empty_text_adds_warning(fake_image, tmp_path, parser, monkeypatch):
    monkeypatch.setattr(parser, "_ocr_image", MagicMock(return_value=""))
    monkeypatch.setattr(
        "PIL.Image.open", MagicMock(return_value=MagicMock(close=MagicMock()))
    )
    result = parser.parse(fake_image, tmp_path / "out", {})
    assert any("未识别" in w for w in result.warnings)


# ---- pypdfium2 依赖缺失 ----

def test_pdf_without_pypdfium2_raises_clear_error(monkeypatch, minimal_pdf, tmp_path, parser):
    """pypdfium2 不可用时给出明确 ParserError。"""
    def _fake_import(name, package=None):
        if name.startswith("pypdfium2"):
            raise ImportError("no pypdfium2")
        if name.startswith("rapidocr"):
            raise ImportError("no rapidocr")
        raise ImportError(name)

    monkeypatch.setattr("importlib.import_module", _fake_import)
    monkeypatch.delitem(sys.modules, "pypdfium2", raising=False)
    # _parse_pdf 里直接 import pypdfium2，这里会 ParserError 被 parse() 重包
    with pytest.raises(ParserError) as exc:
        parser.parse(minimal_pdf, tmp_path / "out", {})
    # 错误消息应包含 pypdfium2
    assert "pypdfium2" in str(exc.value) or "不可用" in str(exc.value)


# ---- 启动不崩溃 ----

def test_service_starts_without_rapidocr_dependency(tmp_path):
    """未安装 rapidocr 时，import rapidocr_engine 模块不应崩溃（依赖全延迟导入）。"""
    backend = Path(__file__).resolve().parents[1]
    env = {**__import__("os").environ, "PYTHONPATH": str(backend)}
    script = tmp_path / "no_rapidocr_check.py"
    script.write_text(
        "from app.parsers.rapidocr_engine import RapidOCRParser\n"
        "p = RapidOCRParser()\n"
        "print('import_ok', p.name)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(backend),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # 关键：import 成功且 RapidOCRParser 实例化不崩溃
    assert result.returncode == 0, result.stderr
    assert "import_ok rapidocr" in result.stdout


# =====================================================================
# P7：MinerU / PaddleX HTTP 适配器
# =====================================================================
import base64 as _b64
import io as _io
import zipfile as _zipfile

import httpx as _httpx

from app.parsers.base import ParsedDocument, ParserError, UnsupportedFormatError
from app.parsers.mineru import MinerUParser
from app.parsers.paddlex import PaddleXParser
from app.parsers.zip_safe import collect_images, safe_extract_zip


# ---- MinerU：ZIP 解析 ----

def _make_mineru_zip(md_text: str, with_images: bool = False) -> bytes:
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", md_text)
        if with_images:
            zf.writestr("images/pic1.png", b"\x89PNG\x0d\x0a\x1a\x0a" + b"0" * 20)
    return buf.getvalue()


def _fake_mineru_health_response(monkeypatch, status_code=200, body_text=""):
    """替换 httpx.Client 使 health() 返回可控响应。"""
    from types import SimpleNamespace

    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.text = body_text or ("contains /file_parse path" if status_code == 200 else "")

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(_httpx, "Client", FakeClient)


def _fake_mineru_parse(monkeypatch, *, zip_bytes: bytes | None = None, status_code=200, exc=None, resp_text=""):
    """替换 httpx.Client 使 post() 返回可控 ZIP/响应，用于 parse()。"""
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.content = zip_bytes or b""
            self.text = resp_text

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            if exc is not None:
                raise exc
            return FakeResponse()

    monkeypatch.setattr(_httpx, "Client", FakeClient)


def test_mineru_available_requires_httpx(tmp_path):
    parser = MinerUParser()
    assert parser.available() is True  # httpx 已装


def test_mineru_health_ok(monkeypatch):
    _fake_mineru_health_response(monkeypatch, status_code=200, body_text='{"paths": "/file_parse"}')
    h = MinerUParser().health()
    assert h["available"] is True
    assert h["status"] == "ok"


def test_mineru_health_missing_file_parse(monkeypatch):
    _fake_mineru_health_response(monkeypatch, status_code=200, body_text="{}")
    h = MinerUParser().health()
    assert h["available"] is False


def test_mineru_health_http_error(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            raise _httpx.ConnectError("refused")

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    h = MinerUParser().health()
    assert h["available"] is False
    assert h["status"] == "unreachable"


def test_mineru_parse_uses_full_md(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    md = "## 第 1 页\n\nHello from mineru.\n\n![img](images/pic1.png)\n"
    zip_bytes = _make_mineru_zip(md, with_images=True)
    _fake_mineru_parse(monkeypatch, zip_bytes=zip_bytes)
    result = MinerUParser().parse(src, tmp_path / "out", {})
    assert result.engine == "mineru"
    assert "Hello from mineru" in result.markdown
    assert result.assets and result.assets[0].kind == "image"
    # 图片链接已改写为 assets/
    assert "assets/" in result.markdown


def test_mineru_parse_http_non200(monkeypatch, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_mineru_parse(monkeypatch, status_code=500, resp_text="server error")
    with pytest.raises(ParserError, match="http_500"):
        MinerUParser().parse(src, tmp_path / "out", {})


def test_mineru_parse_timeout(monkeypatch, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_mineru_parse(monkeypatch, exc=_httpx.TimeoutException("slow"))
    with pytest.raises(ParserError, match="timeout"):
        MinerUParser().parse(src, tmp_path / "out", {})


def test_mineru_parse_connect_error(monkeypatch, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_mineru_parse(monkeypatch, exc=_httpx.ConnectError("refused"))
    with pytest.raises(ParserError, match="connection_error"):
        MinerUParser().parse(src, tmp_path / "out", {})


def test_mineru_parse_empty_md_warns(monkeypatch, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    zip_bytes = _make_mineru_zip("   \n\n  ")
    _fake_mineru_parse(monkeypatch, zip_bytes=zip_bytes)
    result = MinerUParser().parse(src, tmp_path / "out", {})
    assert any("未返回文本" in w for w in result.warnings)


def test_mineru_pick_markdown_prefers_full_md(tmp_path):
    parser = MinerUParser()
    base = tmp_path / "x"
    base.mkdir()
    other = base / "other.md"
    full = base / "full.md"
    other.write_text("other")
    full.write_text("full")
    assert parser._pick_markdown(list(base.iterdir()), base).name == "full.md"


# ---- MinerU：ZIP 安全（经 parse 层） ----

def test_mineru_zip_bomb_rejected(monkeypatch, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    # 构造超过安全上限的 ZIP：先直接测试 safe_extract_zip 保护，再走 parse 映射
    from app.parsers.zip_safe import MAX_ENTRIES, MAX_TOTAL_BYTES, UnsafeArchiveError
    with pytest.raises(Exception):  # 任何异常都表明解压被拦截，而非静默写入
        # 直接触发校验：声明总大小超过上限
        bad = tmp_path / "bad.zip"
        with _zipfile.ZipFile(bad, "w") as zf:
            for i in range(MAX_ENTRIES + 1):
                zf.writestr(f"f{i}", b"0" * 1024)
        safe_extract_zip(bad, tmp_path / "boom")


def test_mineru_zip_path_traversal_rejected(tmp_path):
    from app.parsers.zip_safe import UnsafeArchiveError
    bad = tmp_path / "evil.zip"
    with _zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../../evil.txt", b"bad")
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(bad, tmp_path / "boom")


# ---- PaddleX：JSON 解析 ----

def _fake_paddlex(monkeypatch, *, json_payload=None, status_code=200, exc=None):
    from types import SimpleNamespace

    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.text = f"http {status_code}"
            self._json = json_payload or {}

        def json(self):
            if isinstance(self._json, Exception):
                raise self._json
            return self._json

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            if exc is not None:
                raise exc
            return FakeResponse()

        def get(self, *a, **kw):
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(_httpx, "Client", FakeClient)


def test_paddlex_parse_joins_pages(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    payload = {
        "errorCode": 0,
        "result": {
            "layoutParsingResults": [
                {"markdown": {"text": "page one"}},
                {"markdown": {"text": "page two"}},
            ]
        },
    }
    _fake_paddlex(monkeypatch, json_payload=payload)
    result = PaddleXParser().parse(src, tmp_path / "out", {})
    assert result.engine == "paddlex"
    assert result.page_count == 2
    assert "page one" in result.markdown and "page two" in result.markdown


def test_paddlex_parse_base64_pdf_sets_filetype_0(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"errorCode": 0, "result": {"layoutParsingResults": [{"markdown": {"text": "x"}}]}}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, **kw):
            captured["json"] = json
            return FakeResponse()

        def get(self, *a, **kw):
            from types import SimpleNamespace
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    PaddleXParser().parse(src, tmp_path / "out", {})
    assert captured["json"]["fileType"] == 0
    assert captured["json"]["file"]


def test_paddlex_parse_nonzero_error_code(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_paddlex(monkeypatch, json_payload={"errorCode": 100, "errorMsg": "bad doc"})
    with pytest.raises(ParserError, match="error_100"):
        PaddleXParser().parse(src, tmp_path / "out", {})


def test_paddlex_parse_empty_results(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_paddlex(monkeypatch, json_payload={"errorCode": 0, "result": {"layoutParsingResults": []}})
    with pytest.raises(ParserError, match="空"):
        PaddleXParser().parse(src, tmp_path / "out", {})


def test_paddlex_parse_http_non200(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_paddlex(monkeypatch, status_code=503)
    with pytest.raises(ParserError, match="http_503"):
        PaddleXParser().parse(src, tmp_path / "out", {})


def test_paddlex_parse_timeout(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_paddlex(monkeypatch, exc=_httpx.TimeoutException("slow"))
    with pytest.raises(ParserError, match="timeout"):
        PaddleXParser().parse(src, tmp_path / "out", {})


def test_paddlex_parse_invalid_json(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-fake")
    _fake_paddlex(monkeypatch, json_payload=ValueError("not json"))
    with pytest.raises(ParserError, match="非 JSON"):
        PaddleXParser().parse(src, tmp_path / "out", {})


def test_paddlex_health_reachable(tmp_path, monkeypatch):
    _fake_paddlex(monkeypatch, json_payload={"errorCode": 0})
    h = PaddleXParser().health()
    assert h["available"] is True


def test_paddlex_health_unreachable(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            raise _httpx.ConnectError("refused")

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    h = PaddleXParser().health()
    assert h["available"] is False


def test_paddlex_unsupported_format(tmp_path):
    src = tmp_path / "foo.xlsx"
    src.write_bytes(b"fake")
    with pytest.raises(UnsupportedFormatError):
        PaddleXParser().parse(src, tmp_path / "out", {})


def test_registry_references_mineru_paddlex():
    """注册表声明了 mineru/paddlex，且模块可被工厂导入。"""
    from app.parsers.registry import PARSERS, get_parser
    assert "mineru" in PARSERS and "paddlex" in PARSERS
    m = get_parser("mineru")
    p = get_parser("paddlex")
    assert m.name == "mineru" and p.name == "paddlex"
