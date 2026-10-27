"""阶段 2 测试：种子 URL 清单校验。

验收点：
1. 四个语言种子文件（java/python/cpp/go.txt）存在且非空；
2. 每行都是合法 http(s) URL；
3. 每个 URL 的域名都已登记在 knowledge_sources 中（阶段 1 注册表）；
4. 每个文件至少包含一条学习路线（roadmap.sh）与一条教程入口。
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from scripts.manage_sources import KnowledgeSourceRegistry

SEEDS_DIR = get_settings().data_dir / "crawl_seeds"
LANGS = ["java", "python", "cpp", "go"]


def _registered_domains() -> set[str]:
    reg = KnowledgeSourceRegistry(get_settings().mysql_url)
    domains: set[str] = set()
    for row in reg.list_sources():
        for d in str(row["domains"]).split(","):
            if d.strip():
                domains.add(d.strip().lower())
    return domains


def _domain_matches(url: str, registered: set[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in registered)


@pytest.mark.parametrize("lang", LANGS)
def test_seed_file_exists_and_urls_valid(lang):
    path = SEEDS_DIR / f"{lang}.txt"
    assert path.exists(), f"缺少种子文件 {path}"
    urls = [u.strip() for u in path.read_text("utf-8").splitlines() if u.strip() and not u.startswith("#")]
    assert len(urls) >= 3, f"{lang}.txt 种子过少"
    for url in urls:
        parsed = urlparse(url)
        assert parsed.scheme in ("http", "https"), f"非法 URL: {url}"
        assert parsed.netloc, f"非法 URL: {url}"


@pytest.mark.parametrize("lang", LANGS)
def test_seed_urls_registered_in_sources(lang):
    path = SEEDS_DIR / f"{lang}.txt"
    urls = [u.strip() for u in path.read_text("utf-8").splitlines() if u.strip() and not u.startswith("#")]
    registered = _registered_domains()
    for url in urls:
        assert _domain_matches(url, registered), f"{url} 的域名未在 knowledge_sources 注册"


@pytest.mark.parametrize("lang", LANGS)
def test_seed_has_roadmap_and_tutorial(lang):
    path = SEEDS_DIR / f"{lang}.txt"
    text = path.read_text("utf-8")
    assert "roadmap.sh" in text, f"{lang} 缺少学习路线源"
    assert ("tutorial" in text) or ("%E9%A6%96" in text), f"{lang} 缺少教程入口"
