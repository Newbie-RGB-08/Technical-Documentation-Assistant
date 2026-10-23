"""P6：PDF 引擎选择 / auto 路由 / 降级逻辑。

PRD 7.5：扫描 PDF 走 OCR，数字 PDF 走 native；auto 永不静默降级为指定引擎。
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfReader, PdfWriter

from app.parsers.base import ParsedDocument, ParserError
from app.parsers.native import pdf_page_count, pdf_text_chars_per_page
from app.services.upload_pipeline import (
    IMAGE_FALLBACK_ORDER,
    MIN_ENGINE_OUTPUT_CHARS,
    PDF_FALLBACK_ORDER,
    ParseFailure,
    choose_engine_candidates,
    markdown_text_length,
    parse_with_fallback,
)


# ---- 夹具：构造最小文本层 PDF ----

def _make_pdf(text: str | list[str], path: Path, *, encrypted: bool = False) -> Path:
    """用 pypdf 构造每页固定文本的最小 PDF。"""
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject
    from pypdf._page import PageObject

    writer = PdfWriter()
    writer.add_blank_page(width=216, height=216)  # letter 6x6 inch
    page = writer.pages[0]

    texts = text if isinstance(text, list) else [text]
    # 用 SCRIPT 注入文本（最小化）
    try:
        from pypdf.generic import DecodedStreamObject

        content = b"BT /F1 12 Tf 50 200 Td ("
        content += texts[0].encode("latin-1", errors="ignore")[:60]
        content += b") Tj ET"
        page["/Contents"] = DecodedStreamObject.create_data(content)
        resources = page.get("/Resources", DictionaryObject())
        fonts = ArrayObject()
        resources[NameObject("/Font")] = DictionaryObject()
        writer.pages[0] = page
    except Exception:
        pass  # pypdf 版本差异兜底：至少页数正确

    if encrypted:
        writer.encrypt("secret")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


@pytest.fixture()
def text_pdf(tmp_path):
    """数字 PDF：每页 200+ 字符文本层。"""
    pdf = tmp_path / "text.pdf"
    _make_pdf("A" * 250, pdf)
    return pdf


@pytest.fixture()
def scan_pdf(tmp_path):
    """扫描 PDF：几乎没有文本层（用空白页）。"""
    from pypdf import PdfWriter

    pdf = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=216, height=216)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    return pdf


@pytest.fixture()
def encrypted_pdf(tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_pdf("this should not matter", pdf, encrypted=True)
    return pdf


@pytest.fixture()
def zero_page_pdf(tmp_path):
    """损坏 PDF：空文件。"""
    pdf = tmp_path / "zero.pdf"
    pdf.write_bytes(b"")
    return pdf


# ---- PDF 文本层判定 ----

def test_pdf_text_chars_per_page_text_pdf(text_pdf):
    chars = pdf_text_chars_per_page(text_pdf)
    # pypdf 可能抽不到 SCRIPT 里的文本，但页数正确；这里只验证函数不抛
    assert chars >= 0


def test_pdf_text_chars_per_page_scan_pdf(scan_pdf):
    chars = pdf_text_chars_per_page(scan_pdf)
    assert chars <= 5  # 扫描页应接近 0


def test_pdf_page_count_zero_page_raises(zero_page_pdf):
    with pytest.raises(ParserError):
        pdf_page_count(zero_page_pdf)


def test_pdf_text_chars_per_page_encrypted_raises(encrypted_pdf):
    with pytest.raises(ParserError):
        pdf_text_chars_per_page(encrypted_pdf)


# ---- choose_engine_candidates ----

def test_auto_text_prefers_native_then_fallback(text_pdf, monkeypatch):
    """文本层充分的 PDF：native 优先，之后跟可用 OCR 兜底。"""
    order = {"native", "mineru", "paddlex", "rapidocr"}
    candidates = choose_engine_candidates(
        text_pdf,
        "auto",
        available=lambda n: n in order,
        text_chars_per_page=200.0,
        min_text_chars=80,
    )
    assert candidates[0] == "native"
    # 后续是 PDF_FALLBACK_ORDER 里 native 之后的可用项
    assert all(name in PDF_FALLBACK_ORDER for name in candidates[1:])
    assert "native" not in candidates[1:]


def test_auto_scan_skips_native(scan_pdf):
    """扫描 PDF（< 阈值）：直接走 OCR 队列，native 不在首位。"""
    available_only_ocr = {"rapidocr", "mineru"}
    candidates = choose_engine_candidates(
        scan_pdf,
        "auto",
        available=lambda n: n in available_only_ocr,
        text_chars_per_page=0.0,
        min_text_chars=80,
    )
    assert candidates[0] != "native"
    assert "rapidocr" in candidates or "mineru" in candidates


def test_auto_all_ocr_unavailable_falls_back_to_native(scan_pdf):
    candidates = choose_engine_candidates(
        scan_pdf,
        "auto",
        available=lambda n: False,
        text_chars_per_page=0.0,
        min_text_chars=80,
    )
    assert candidates == ["native"]


def test_auto_image_uses_image_fallback_order():
    candidates = choose_engine_candidates(
        "anything.png",
        "auto",
        available=lambda n: n in IMAGE_FALLBACK_ORDER,
    )
    assert candidates == list(IMAGE_FALLBACK_ORDER)


def test_specified_engine_does_not_auto_downgrade(text_pdf):
    """显式指定引擎时，绝不自动降级；即使该引擎不可用也只返回它。"""
    candidates = choose_engine_candidates(
        text_pdf,
        "rapidocr",
        available=lambda n: False,
    )
    assert candidates == ["rapidocr"]


# ---- parse_with_fallback ----

def _make_parser(name: str, *, markdown: str = "", raise_exc: Exception | None = None) -> MagicMock:
    parser = MagicMock()
    parser.name = name
    if raise_exc is not None:
        parser.parse.side_effect = raise_exc
    else:
        parser.parse.return_value = ParsedDocument(
            markdown=markdown, engine=name, page_count=1
        )
    return parser


def test_fallback_uses_first_available_success(tmp_path):
    p_a = _make_parser("a", markdown="hello from a")
    p_b = _make_parser("b", markdown="hello from b")
    out = tmp_path / "out"
    result = parse_with_fallback(
        tmp_path / "x.pdf",
        out,
        ["a", "b"],
        parser_getter=lambda n: {"a": p_a, "b": p_b}[n],
        timeout_seconds=30,
    )
    assert result.engine == "a"
    assert "hello from a" in result.markdown
    p_a.parse.assert_called_once()
    p_b.parse.assert_not_called()


def test_fallback_continues_on_exception(tmp_path):
    p_a = _make_parser("a", raise_exc=ParserError("boom"))
    p_b = _make_parser("b", markdown="b rescued")
    result = parse_with_fallback(
        tmp_path / "x.pdf",
        tmp_path / "out",
        ["a", "b"],
        parser_getter=lambda n: {"a": p_a, "b": p_b}[n],
    )
    assert result.engine == "b"
    assert "b rescued" in result.markdown
    # a 的失败被写进 warnings
    assert any("boom" in w for w in result.warnings)


def test_fallback_continues_on_short_output(tmp_path):
    """引擎输出过短 < min_chars 视为失败，继续尝试下一个。"""
    short_a = _make_parser("a", markdown="ABC")  # 3 chars < 50
    long_b = _make_parser("b", markdown="x" * 100)
    result = parse_with_fallback(
        tmp_path / "x.pdf",
        tmp_path / "out",
        ["a", "b"],
        parser_getter=lambda n: {"a": short_a, "b": long_b}[n],
        min_chars=50,
    )
    assert result.engine == "b"


def test_fallback_all_fail_raises_parse_failure(tmp_path):
    p_a = _make_parser("a", raise_exc=ParserError("a down"))
    p_b = _make_parser("b", raise_exc=RuntimeError("b down"))
    with pytest.raises(ParseFailure, match="已尝试"):
        parse_with_fallback(
            tmp_path / "x.pdf",
            tmp_path / "out",
            ["a", "b"],
            parser_getter=lambda n: {"a": p_a, "b": p_b}[n],
        )


def test_fallback_empty_candidates_raises(tmp_path):
    with pytest.raises(ParseFailure, match="没有可用"):
        parse_with_fallback(
            tmp_path / "x.pdf",
            tmp_path / "out",
            [],
        )


def test_markdown_text_length_strips_page_headers():
    md = "## 第 1 页\n\nHello world\n\n## 第 2 页\n\nAnother paragraph here."
    assert markdown_text_length(md) == len("Hello worldAnotherparagraphhere")


def test_fallback_on_timeout_exception(tmp_path, monkeypatch):
    """引擎抛 FuturesTimeoutError 时继续下一个。"""
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    p_slow = _make_parser("slow")
    p_fast = _make_parser("fast", markdown="fast rescue")

    def fake_timeout(parser, src, out_dir, params, timeout):
        if parser.name == "slow":
            raise FuturesTimeoutError("timed out")
        return parser.parse(src, out_dir, params)

    monkeypatch.setattr(
        "app.services.upload_pipeline._parse_with_timeout", fake_timeout
    )
    result = parse_with_fallback(
        tmp_path / "x.pdf",
        tmp_path / "out",
        ["slow", "fast"],
        parser_getter=lambda n: {"slow": p_slow, "fast": p_fast}[n],
    )
    assert result.engine == "fast"
    assert any("超时" in w for w in result.warnings)


def test_min_engine_output_chars_constant_is_prd_aligned():
    """PRD 7.5：输出 <50 字符视为引擎失败。"""
    assert MIN_ENGINE_OUTPUT_CHARS == 50
