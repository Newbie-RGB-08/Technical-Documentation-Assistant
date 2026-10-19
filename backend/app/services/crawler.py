"""技术文档网站爬取（PRD 3.1.1）。

优先使用 Crawl4AI（HTML→Markdown，保留代码块与表格）；
导入失败时降级为 httpx + BeautifulSoup + html2text，接口不变。
产出与本地笔记同构的文件级元数据，Markdown 落盘到 tech_docs_dir。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse

import httpx

try:  # Crawl4AI 主路径
    from crawl4ai import AsyncWebCrawler  # noqa: F401

    HAS_CRAWL4AI = True
except Exception:  # noqa: BLE001 - 任何导入失败都走降级
    HAS_CRAWL4AI = False

import html2text
from bs4 import BeautifulSoup

NOISE_SELECTORS = [
    "nav", "aside", "footer", "header",
    ".sidebar", "#sidebar", ".nav", ".footer", ".breadcrumb",
    ".pagination", ".toc", "#toc", "script", "style", "noscript",
]

_h2t = html2text.HTML2Text()
_h2t.body_width = 0
_h2t.ignore_images = True
_h2t.ignore_emphasis = False
_h2t.mark_code = True

# html2text 的 mark_code 输出 [code]\n    缩进代码\n[/code]，恢复为 ``` 围栏以便分块器保护
_CODE_MARKER_RE = re.compile(r"\[code\][ \t]*\n(.*?)[ \t]*\[/code\]", re.DOTALL)


def _restore_code_fences(md: str) -> str:
    def repl(m: re.Match) -> str:
        lines = [
            (ln[4:] if ln.startswith("    ") else ln).rstrip()
            for ln in m.group(1).split("\n")
        ]
        return "```\n" + "\n".join(lines).strip("\n") + "\n```"

    return _CODE_MARKER_RE.sub(repl, md)


def html_to_markdown(html: str, base_url: str = "") -> tuple[str, str]:
    """HTML → (markdown, title)。去噪后转换，保留代码块与表格。"""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    for sel in NOISE_SELECTORS:
        for node in soup.select(sel):
            node.decompose()
    main = (
        soup.select_one("main")
        or soup.select_one("article")
        or soup.select_one(".content")
        or soup.body
        or soup
    )
    md = _restore_code_fences(_h2t.handle(str(main)).strip())
    if base_url:
        # 相对链接转绝对链接，便于溯源
        md = re.sub(
            r"\]\((/[^)]*)\)", lambda m: f"]({urljoin(base_url, m.group(1))})", md
        )
    return md, title


def extract_links(html: str, base_url: str, same_domain_only: bool = True) -> list[str]:
    """提取页面内的同域链接（去锚点、去重、保持顺序）。"""
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).netloc
    seen: list[str] = []
    for a in soup.find_all("a", href=True):
        raw = urldefrag(urljoin(base_url, a["href"]))[0]
        if not raw.startswith(("http://", "https://")):
            continue
        if same_domain_only and urlparse(raw).netloc != host:
            continue
        if raw not in seen:
            seen.append(raw)
    return seen


def slugify(url: str) -> str:
    """URL → 安全文件名。"""
    parsed = urlparse(url)
    path = (parsed.netloc + parsed.path).strip("/")
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", path).strip("_")
    return (slug or "index")[:120]


async def crawl_page(url: str, delay: float = 2.0) -> dict:
    """爬取单个页面，返回 {url, title, markdown}。请求间遵守延时礼仪。"""
    if HAS_CRAWL4AI:
        try:
            from crawl4ai import AsyncWebCrawler

            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url)
                if result.success and result.markdown:
                    return {
                        "url": url,
                        "title": result.metadata.get("title", "") or url,
                        "markdown": result.markdown,
                    }
        except Exception:  # noqa: BLE001 - 主路径任何异常都降级
            pass

    headers = {"User-Agent": "Mozilla/5.0 (compatible; PTDRA/1.0; personal research)"}
    async with httpx.AsyncClient(headers=headers, timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        await asyncio.sleep(delay)
    md, title = html_to_markdown(resp.text, base_url=url)
    return {"url": url, "title": title or url, "markdown": md}


async def crawl_site(
    start_urls: list[str],
    max_depth: int = 1,
    max_pages: int = 20,
    delay: float = 2.0,
) -> list[dict]:
    """BFS 爬取：同域链接，深度 max_depth，总量 max_pages，间隔 delay 秒。"""
    results: dict[str, dict] = {}
    frontier = [(u, 0) for u in start_urls]
    visited: set[str] = set()

    while frontier and len(results) < max_pages:
        url, depth = frontier.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            page = await crawl_page(url, delay=delay)
        except Exception:  # noqa: BLE001 - 单页失败不阻断整站
            continue
        results[url] = page

        if depth < max_pages and depth < max_depth:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; PTDRA/1.0)"}
            try:
                async with httpx.AsyncClient(headers=headers, timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                    links = extract_links(resp.text, base_url=url)
            except Exception:  # noqa: BLE001
                links = []
            for link in links:
                if link not in visited:
                    frontier.append((link, depth + 1))

    return list(results.values())


def save_crawled(pages: list[dict], out_dir: str | Path) -> list[dict]:
    """爬取结果落盘为带 front-matter 的 Markdown，返回文件级元数据列表。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    saved: list[dict] = []
    for page in pages:
        path = out / f"{slugify(page['url'])}.md"
        fm = (
            "---\n"
            f"title: {page['title']}\n"
            f"source_url: {page['url']}\n"
            f"created_at: {now}\n"
            f"updated_at: {now}\n"
            "source_type: tech_doc\n"
            "---\n\n"
        )
        path.write_text(fm + page["markdown"], encoding="utf-8")
        saved.append(
            {
                "file_path": str(path),
                "source_type": "tech_doc",
                "title": page["title"],
                "tags": [],
                "created_at": now,
                "updated_at": now,
                "content": page["markdown"],
            }
        )
    return saved
