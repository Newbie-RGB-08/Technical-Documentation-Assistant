"""P3：检索编排（三路由、融合重排、配额、连续编号、故障降级）。"""
import asyncio

from app.services.rag_service import build_sources
from app.services.retrieval_orchestrator import (
    extract_paragraphs,
    orchestrate,
)
from app.services.web_fetcher import FetchedPage
from app.services.web_search import WebResult

QUERY = "ChromaDB 的 PersistentClient 如何启动？"


def _local_chunk(i, text=None, rerank=0.9, path=None):
    return {
        "id": f"c{i}",
        "text": text or f"ChromaDB 的 PersistentClient 通过持久化路径启动，片段 {i}。",
        "metadata": {
            "file_path": path or f"data/notes/a{i}.md",
            "title": f"文档{i}",
            "source_type": "personal_note",
            "source_url": "",
            "heading_path": "/ 启动",
        },
        "rerank_score": rerank,
    }


def _page(markdown=None, url="https://docs.example.com/chroma"):
    return FetchedPage(
        url=url,
        title="ChromaDB 官方文档",
        markdown=markdown
        or (
            "ChromaDB 的 PersistentClient 通过持久化路径启动，"
            "需要在创建客户端时传入 path 参数并保持进程内单例。"
            "这样可以避免重复打开数据库文件造成的锁竞争问题。"
        ),
        provider="tavily",
        retrieved_at="2026-10-21T12:00:00+08:00",
    )


class FakeLocal:
    def __init__(self, chunks=None, level=3, confidence=0.9, error=None):
        self.chunks = chunks if chunks is not None else [_local_chunk(1)]
        self.level = level
        self.confidence = confidence
        self.error = error
        self.calls = 0

    def __call__(self, query, level=3, top_k=5):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.chunks), self.level, self.confidence


class FakeGateway:
    def __init__(self, results=None, error=None):
        self.results = results if results is not None else [WebResult(url="https://docs.example.com/chroma")]
        self.error = error
        self.calls = 0

    async def search(self, query, top_k=None):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.results)


class FakeFetcher:
    def __init__(self, pages=None):
        self.pages = pages if pages is not None else [_page()]
        self.calls = 0

    async def __call__(self, results, **kwargs):
        self.calls += 1
        return list(self.pages)


class FakeReranker:
    def __init__(self, scores=None, error=None):
        self.scores = scores
        self.error = error
        self.calls = 0

    def rerank(self, query, documents):
        self.calls += 1
        if self.error:
            raise self.error
        if self.scores is not None:
            return list(self.scores)[: len(documents)]
        return [1.0 - i * 0.01 for i in range(len(documents))]


def _run(coro):
    return asyncio.run(coro)


def test_simple_hit_does_not_search_web():
    local = FakeLocal([_local_chunk(1)])
    gateway = FakeGateway()
    fetcher = FakeFetcher()

    outcome = _run(
        orchestrate(
            QUERY,
            local_retriever=local,
            complexity="simple",
            route="fallback",
            web_gateway=gateway,
            fetcher=fetcher,
        )
    )

    assert local.calls == 1
    assert gateway.calls == 0 and fetcher.calls == 0
    assert outcome.web_used is False
    assert outcome.answerability.value == "HIT"
    assert outcome.chunks[0]["metadata"]["source_type"] == "personal_note"


def test_medium_miss_escalates_to_web():
    local = FakeLocal([], level=3, confidence=0.0)
    gateway = FakeGateway()
    fetcher = FakeFetcher()

    outcome = _run(
        orchestrate(
            "ChromaDB PersistentClient 如何启动？",
            local_retriever=local,
            complexity="medium",
            route="fallback",
            web_gateway=gateway,
            fetcher=fetcher,
        )
    )

    assert gateway.calls == 1 and fetcher.calls == 1
    assert outcome.web_used is True
    assert outcome.answerability.value == "HIT"
    web_meta = outcome.chunks[0]["metadata"]
    assert web_meta["origin"] == "web_search"
    assert web_meta["source_url"] == "https://docs.example.com/chroma"
    assert web_meta["retrieved_at"]


def test_complex_runs_local_and_web_together():
    local = FakeLocal([_local_chunk(1, rerank=0.6)])
    gateway = FakeGateway()
    fetcher = FakeFetcher()

    outcome = _run(
        orchestrate(
            QUERY,
            local_retriever=local,
            complexity="complex",
            route="parallel",
            web_gateway=gateway,
            fetcher=fetcher,
        )
    )

    assert local.calls == 1
    assert gateway.calls == 1 and fetcher.calls == 1
    assert outcome.web_used is True
    origins = {c["metadata"].get("origin", "local") for c in outcome.chunks}
    assert origins == {"local", "web_search"}

    sources = build_sources(outcome.chunks)
    indices = sorted(i for card in sources for i in card["indices"])
    assert indices == list(range(1, len(outcome.chunks) + 1))


def test_quota_limits_total_local_web_and_per_document():
    local_chunks = [
        _local_chunk(i, path=f"data/notes/doc{i // 2}.md", rerank=0.9 - i * 0.01)
        for i in range(8)
    ]
    web_pages = [
        _page(url=f"https://docs.example.com/p{i}") for i in range(4)
    ]
    reranker = FakeReranker(
        scores=[1.0 - i * 0.001 for i in range(8 + 4 * 2)]
    )

    outcome = _run(
        orchestrate(
            QUERY,
            local_retriever=FakeLocal(local_chunks),
            complexity="complex",
            route="parallel",
            top_k=5,
            web_gateway=FakeGateway(),
            fetcher=FakeFetcher(web_pages),
            reranker=reranker,
        )
    )

    assert len(outcome.chunks) <= 6
    local_used = [c for c in outcome.chunks if c["metadata"].get("origin") != "web_search"]
    web_used = [c for c in outcome.chunks if c["metadata"].get("origin") == "web_search"]
    assert len(local_used) <= 5
    assert len(web_used) <= 3

    per_doc: dict[str, int] = {}
    for chunk in local_used:
        path = chunk["metadata"]["file_path"]
        per_doc[path] = per_doc.get(path, 0) + 1
    assert max(per_doc.values()) <= 2


def test_reranker_failure_falls_back_to_rrf_order():
    local = FakeLocal([_local_chunk(1, rerank=0.9)])
    reranker = FakeReranker(error=RuntimeError("model missing"))

    outcome = _run(
        orchestrate(
            QUERY,
            local_retriever=local,
            complexity="complex",
            route="parallel",
            web_gateway=FakeGateway(),
            fetcher=FakeFetcher(),
            reranker=reranker,
        )
    )

    assert outcome.chunks
    assert "rerank_score" not in outcome.chunks[0] or outcome.chunks[0]["rerank_score"] == 0.9


def test_web_failure_keeps_local_results():
    local = FakeLocal([_local_chunk(1)])
    outcome = _run(
        orchestrate(
            QUERY,
            local_retriever=local,
            complexity="complex",
            route="parallel",
            web_gateway=FakeGateway(error=RuntimeError("search down")),
            fetcher=FakeFetcher(),
        )
    )

    assert outcome.chunks
    assert outcome.web_used is False


def test_local_failure_still_uses_web():
    local = FakeLocal(error=RuntimeError("chroma down"))
    outcome = _run(
        orchestrate(
            QUERY,
            local_retriever=local,
            complexity="medium",
            route="fallback",
            web_gateway=FakeGateway(),
            fetcher=FakeFetcher(),
        )
    )

    assert outcome.web_used is True
    assert outcome.chunks


def test_route_none_disables_web():
    gateway = FakeGateway()
    outcome = _run(
        orchestrate(
            QUERY,
            local_retriever=FakeLocal([], confidence=0.0),
            complexity="simple",
            route="none",
            web_gateway=gateway,
            fetcher=FakeFetcher(),
        )
    )

    assert gateway.calls == 0
    assert outcome.web_used is False


def test_extract_paragraphs_prefers_query_overlap():
    markdown = (
        "这是一段与问题无关的介绍文字，只用于填充长度，确保它可以被当作正文段落。"
        "\n\n"
        "ChromaDB 的 PersistentClient 通过 path 参数指定持久化目录，启动时直接复用。"
        "\n\n"
        "另一段无关内容，同样填充足够的字符长度，方便段落筛选逻辑正常工作。"
    )
    paragraphs = extract_paragraphs(markdown, "ChromaDB PersistentClient 启动", max_n=1)
    assert len(paragraphs) == 1
    assert "PersistentClient" in paragraphs[0]
