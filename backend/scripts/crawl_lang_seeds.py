"""语言学习资料爬取驱动（语言学习资料爬取入库实施方案 步骤 3）。

纯调用现有 crawler 服务模块：读取 data/crawl_seeds/<lang>.txt，
crawl_site（同域 BFS）→ save_crawled 落盘 data/tech_docs/<lang>/。

用法:
  python scripts/crawl_lang_seeds.py --lang java
  python scripts/crawl_lang_seeds.py --lang all
  可选: --max-depth 1 --max-pages 20 --delay 2.5
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.services.crawler import crawl_site, save_crawled

LANGS = ["java", "python", "cpp", "go"]


def read_seeds(lang: str) -> list[str]:
    seed_file = get_settings().data_dir / "crawl_seeds" / f"{lang}.txt"
    return [
        line.strip()
        for line in seed_file.read_text("utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def crawl_one(lang: str, max_depth: int, max_pages: int, delay: float) -> dict:
    urls = read_seeds(lang)
    print(f"[{lang}] seeds={len(urls)} depth={max_depth} max_pages={max_pages} delay={delay}", flush=True)
    pages = asyncio.run(crawl_site(urls, max_depth=max_depth, max_pages=max_pages, delay=delay))
    out = get_settings().tech_docs_dir / lang
    saved = save_crawled(pages, out)
    print(f"[{lang}] crawled={len(pages)} saved={len(saved)} -> {out}", flush=True)
    return {"lang": lang, "crawled": len(pages), "saved": len(saved), "out": str(out)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按语言爬取学习资料")
    parser.add_argument("--lang", required=True, help="java/python/cpp/go 或 all")
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--delay", type=float, default=2.5)
    args = parser.parse_args(argv)

    langs = LANGS if args.lang == "all" else [args.lang]
    for lang in langs:
        if lang not in LANGS:
            parser.error(f"未知语言: {lang}")
        crawl_one(lang, args.max_depth, args.max_pages, args.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
