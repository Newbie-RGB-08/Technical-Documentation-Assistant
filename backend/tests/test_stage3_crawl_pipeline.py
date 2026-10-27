"""阶段 3 测试：爬取管道（crawl_site BFS + save_crawled 落盘 + 可扫描回读）。

crawl_site 的单页抓取 crawl_page 用假实现替换（不访问外网），
链接提取走真实 httpx 访问本地 HTTP 服务，验证 BFS 深度/同域约束/总量上限。
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import crawler
from app.services.note_scanner import scan_markdown_files

PAGES = {
    "/index.html": '<html><head><title>Index</title></head><body>'
    '<a href="/page1.html">p1</a><a href="/page2.html">p2</a>'
    '<a href="https://other.example.com/out">out</a></body></html>',
    "/page1.html": "<html><head><title>P1</title></head><body>content p1</body></html>",
    "/page2.html": "<html><head><title>P2</title></head><body>content p2</body></html>",
}


def _make_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # 静默
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


async def _fake_crawl_page(url: str, delay: float = 0.0) -> dict:
    return {"url": url, "title": url.rsplit("/", 1)[-1], "markdown": f"# {url}\n内容"}


@pytest.fixture()
def local_server():
    server = _make_server()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_crawl_site_bfs_depth_and_same_domain(monkeypatch, local_server):
    monkeypatch.setattr(crawler, "crawl_page", _fake_crawl_page)
    base = local_server
    pages = await crawler.crawl_site(
        [f"{base}/index.html"], max_depth=1, max_pages=10, delay=0
    )
    urls = {p["url"] for p in pages}
    # depth=1：index + 两个同域内链；外域链接不得进入
    assert f"{base}/index.html" in urls
    assert f"{base}/page1.html" in urls
    assert f"{base}/page2.html" in urls
    assert all("other.example.com" not in u for u in urls)


async def test_crawl_site_respects_max_pages(monkeypatch, local_server):
    monkeypatch.setattr(crawler, "crawl_page", _fake_crawl_page)
    base = local_server
    pages = await crawler.crawl_site(
        [f"{base}/index.html"], max_depth=1, max_pages=2, delay=0
    )
    assert len(pages) <= 2


def test_crawl_to_scan_pipeline_roundtrip(tmp_path):
    """save_crawled 落盘的 md 必须能被 ingest 入口的 scan_markdown_files 回读。"""
    pages = [
        {"url": "https://docs.example.com/tutorial/intro", "title": "教程简介", "markdown": "# 简介\n\nHello 语言教程。"},
        {"url": "https://docs.example.com/tutorial/setup", "title": "环境安装", "markdown": "# 安装\n\n下载并安装。"},
    ]
    saved = crawler.save_crawled(pages, tmp_path / "tech_docs" / "lang_x")
    assert len(saved) == 2
    for page, rec in zip(pages, saved):
        assert rec["source_type"] == "tech_doc"
        # 溯源 URL 写入文件 front-matter（由 scanner 回读），不在返回 dict 中
        assert f"source_url: {page['url']}" in Path(rec["file_path"]).read_text("utf-8")
        assert Path(rec["file_path"]).exists()

    scanned = scan_markdown_files(tmp_path / "tech_docs" / "lang_x", source_type="tech_doc")
    assert len(scanned) == 2
    by_title = {s["title"]: s for s in scanned}
    assert by_title["教程简介"]["source_url"] == "https://docs.example.com/tutorial/intro"
    assert "Hello 语言教程。" in by_title["教程简介"]["content"]
