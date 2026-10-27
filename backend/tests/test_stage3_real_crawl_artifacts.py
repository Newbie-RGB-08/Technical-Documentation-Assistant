"""阶段 3 真实产物验收：data/tech_docs/<lang> 四语言目录的落盘质量。

前置：scripts/crawl_lang_seeds.py --lang all 已执行。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings

TECH_DOCS = get_settings().tech_docs_dir
LANGS = ["java", "python", "cpp", "go"]


def _md_files(lang: str) -> list[Path]:
    return sorted((TECH_DOCS / lang).glob("*.md"))


@pytest.mark.parametrize("lang", LANGS)
def test_real_crawl_artifacts(lang):
    files = _md_files(lang)
    assert len(files) >= 15, f"{lang} 目录只有 {len(files)} 个 md，爬取量不足"

    with_source = 0
    with_body = 0
    for f in files:
        text = f.read_text("utf-8", errors="replace")
        if "source_url: https://" in text:
            with_source += 1
        # front-matter 之后的正文须有实质内容（>200 字符）
        body = text.split("---", 2)[-1].strip()
        if len(body) > 200:
            with_body += 1
    assert with_source >= len(files) - 2, f"{lang} 有 {len(files) - with_source} 个文件缺 source_url"
    assert with_body >= len(files) * 0.6, f"{lang} 仅 {with_body}/{len(files)} 个文件有实质正文"
