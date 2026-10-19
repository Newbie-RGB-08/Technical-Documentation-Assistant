"""阶段 1 测试：爬虫 HTML→Markdown 清洗与落盘（离线，不访问网络）。"""
from app.services.crawler import extract_links, html_to_markdown, save_crawled, slugify

FIXTURE_HTML = """<html>
<head><title>Chroma 文档 - 持久化</title></head>
<body>
<nav><a href="/home">首页</a> | <a href="/docs">文档</a></nav>
<aside class="sidebar"><ul><li>目录1</li><li>目录2</li></ul></aside>
<main>
<h1>持久化</h1>
<p>使用 PersistentClient 保存数据到磁盘。</p>
<pre><code class="language-python">client = chromadb.PersistentClient(path="./db")</code></pre>
<table><tr><th>参数</th><th>说明</th></tr><tr><td>path</td><td>存储路径</td></tr></table>
<a href="/docs/paging">下一页</a>
<a href="https://others.com/page">外部链接</a>
</main>
<footer>版权所有 © 某公司</footer>
</body>
</html>"""


def test_html_to_markdown_cleans_noise_and_keeps_code():
    md, title = html_to_markdown(FIXTURE_HTML, base_url="https://docs.example.com/docs/x")
    assert title == "Chroma 文档 - 持久化"
    assert "PersistentClient" in md, "正文应保留"
    assert "client = chromadb.PersistentClient" in md, "代码块应保留"
    assert "存储路径" in md, "表格应保留"
    assert "目录1" not in md, "侧边栏噪声应去除"
    assert "版权所有" not in md, "页脚噪声应去除"
    assert "https://docs.example.com/docs/paging" in md, "相对链接应转绝对"


def test_extract_links_same_domain_only():
    links = extract_links(FIXTURE_HTML, base_url="https://docs.example.com/docs/x")
    assert "https://docs.example.com/docs/paging" in links
    assert all("others.com" not in l for l in links)


def test_slugify():
    assert slugify("https://docs.example.com/api/PersistentClient") == "docs_example_com_api_PersistentClient"
    assert slugify("https://x.com/") == "x_com"


def test_save_crawled_writes_front_matter(tmp_path):
    pages = [
        {
            "url": "https://docs.example.com/persist",
            "title": "持久化",
            "markdown": "# 持久化\n\n正文",
        }
    ]
    saved = save_crawled(pages, tmp_path)
    assert len(saved) == 1
    assert saved[0]["source_type"] == "tech_doc"
    from pathlib import Path as _P

    raw = _P(saved[0]["file_path"]).read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    assert "source_type: tech_doc" in raw
    assert "source_url: https://docs.example.com/persist" in raw
