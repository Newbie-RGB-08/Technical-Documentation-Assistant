"""P4 测试：网页知识写回（URL 规范化、质量门、四层去重、TTL、队列）。"""
import re
from pathlib import Path

from app.services import web_writeback as wb
from app.services.note_scanner import parse_front_matter
from app.services.web_fetcher import FetchedPage
from app.services.web_writeback import WebWriteback, WebWritebackQueue

QUERY = "FastAPI Depends 依赖注入"
URL = "https://fastapi.tiangolo.com/tutorial/dependencies/"
BODY = "# 依赖注入\n\n" + "FastAPI 通过 Depends 声明依赖，函数参数由框架注入。\n" * 12


def _page(url: str = URL, **overrides) -> FetchedPage:
    data = {
        "url": url,
        "title": "FastAPI Depends 实战",
        "markdown": BODY,
        "provider": "tavily",
        "retrieved_at": "2026-03-01T10:00:00+08:00",
    }
    data.update(overrides)
    return FetchedPage(**data)


class FakeMetadata:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.url_docs: dict[str, dict] = {}

    def get_document(self, file_path):
        return self.docs.get(file_path)

    def get_document_by_source_url(self, source_url):
        return self.url_docs.get(source_url)


class FakeIngest:
    def __init__(self):
        self.calls: list[dict] = []


def test_normalize_url_rules():
    assert (
        wb.normalize_url("HTTPS://Example.COM:443/a/?utm_source=x&b=2&a=1#frag")
        == "https://example.com/a?a=1&b=2"
    )
    assert wb.normalize_url("http://Example.com:80/a/") == "http://example.com/a"
    assert wb.normalize_url("https://example.com/a?gclid=1&fbclid=2") == "https://example.com/a"
    assert wb.normalize_url("ftp://example.com/a") == ""
    assert wb.normalize_url("") == ""


def test_check_quality_gates(tmp_path):
    writeback = WebWriteback(tech_docs_dir=tmp_path, min_relevance=0.45)

    assert writeback.check(_page(url="ftp://example.com/x"), relevance=1.0)[1] == "unsupported_url"
    assert writeback.check(_page(url="http://127.0.0.1/x"), relevance=1.0)[1] == "unsafe_host"
    assert writeback.check(_page(url="http://localhost/x"), relevance=1.0)[1] == "unsafe_host"
    assert writeback.check(_page(title="  "), relevance=1.0)[1] == "empty_title"
    assert writeback.check(_page(markdown="太短"), relevance=1.0)[1] == "content_length"
    assert (
        writeback.check(_page(markdown="x" * 200_001), relevance=1.0)[1]
        == "content_length"
    )
    assert writeback.check(_page(), relevance=0.44)[1] == "low_relevance"

    allowed, reason, canonical = writeback.check(_page(), relevance=0.45)
    assert allowed and reason == ""
    assert canonical == URL.rstrip("/")

    capped = WebWriteback(tech_docs_dir=tmp_path, daily_chunk_cap=0)
    assert capped.check(_page(), relevance=1.0)[1] == "daily_cap"


def test_write_creates_deterministic_file_with_trace(tmp_path):
    writeback = WebWriteback(tech_docs_dir=tmp_path)
    result = writeback.write(_page(), query=QUERY, relevance=0.8)

    assert result.written and result.reason == "written"
    assert result.chunk_count > 0

    path = Path(result.file_path)
    assert path.exists()
    assert path.parent == tmp_path / "web"
    assert re.fullmatch(r"fastapi\.tiangolo\.com-[0-9a-f]{8}\.md", path.name)

    content = path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(content)
    assert front_matter["title"] == "FastAPI Depends 实战"
    assert front_matter["source_url"] == URL
    assert front_matter["canonical_url"] == URL.rstrip("/")
    assert front_matter["source_type"] == "tech_doc"
    assert front_matter["origin"] == "web_search"
    assert front_matter["search_query"] == QUERY
    assert front_matter["provider"] == "tavily"
    assert front_matter["retrieved_at"] == "2026-03-01T10:00:00+08:00"
    assert front_matter["expires_at"] == "2026-03-31T10:00:00+08:00"
    assert "溯源：来源" in body and URL in body


def test_write_is_idempotent_within_instance(tmp_path):
    writeback = WebWriteback(tech_docs_dir=tmp_path)
    assert writeback.write(_page(), query=QUERY, relevance=0.8).written

    second = writeback.write(_page(), query=QUERY, relevance=0.8)
    assert second.written is False and second.reason == "duplicate_url"
    assert len(list((tmp_path / "web").glob("*.md"))) == 1


def test_write_skips_same_content_hash(tmp_path):
    writeback = WebWriteback(tech_docs_dir=tmp_path)
    file_path, content, _fm = writeback.build_document(_page(), query=QUERY)

    metadata = FakeMetadata()
    metadata.docs[file_path] = {
        "content_hash": wb.content_sha256(content),
        "source_url": URL,
    }
    writeback.metadata_store = metadata

    result = writeback.write(_page(), query=QUERY, relevance=0.8)
    assert result.written is False and result.reason == "duplicate_content"
    assert not Path(file_path).exists()


def test_write_detects_existing_url_with_new_content(tmp_path):
    writeback = WebWriteback(tech_docs_dir=tmp_path)
    file_path, _content, _fm = writeback.build_document(_page(), query=QUERY)

    metadata = FakeMetadata()
    metadata.url_docs[URL.rstrip("/")] = {"id": 7, "content_hash": "old", "source_url": URL}
    writeback.metadata_store = metadata
    writeback.vector_store = object()  # 触发 ingest 路径

    ingested: list[dict] = []

    def _fake_ingest(records, vector_store, metadata_store, **kwargs):
        ingested.append({"records": records, "kwargs": kwargs})
        metadata_store.docs[file_path] = {"id": 7, "content_hash": "new", "source_url": URL}
        return {"ingested": 1, "skipped": 0, "failed": 0, "total_chunks": 2, "errors": []}

    original = wb.ingest_files
    wb.ingest_files = _fake_ingest
    try:
        result = writeback.write(_page(), query=QUERY, relevance=0.8)
    finally:
        wb.ingest_files = original

    assert result.written and result.document_id == 7 and result.chunk_count == 2
    assert ingested and ingested[0]["kwargs"]["force_reingest"] is True


def test_write_failure_records_trace_and_keeps_file(tmp_path):
    writeback = WebWriteback(tech_docs_dir=tmp_path)
    metadata = FakeMetadata()
    writeback.metadata_store = metadata
    writeback.vector_store = object()

    def _failing_ingest(*_args, **_kwargs):
        return {"ingested": 0, "skipped": 0, "failed": 1, "total_chunks": 0, "errors": []}

    original = wb.ingest_files
    wb.ingest_files = _failing_ingest
    try:
        result = writeback.write(_page(), query=QUERY, relevance=0.8)
    finally:
        wb.ingest_files = original

    assert result.written is False and result.reason == "ingest_failed"


def test_on_ingested_callback_runs_after_success(tmp_path):
    events: list[dict] = []
    writeback = WebWriteback(tech_docs_dir=tmp_path, on_ingested=events.append)
    metadata = FakeMetadata()
    writeback.metadata_store = metadata
    writeback.vector_store = object()  # 触发 ingest 路径

    def _ok_ingest(records, vector_store, metadata_store, **kwargs):
        metadata_store.docs[records[0]["file_path"]] = {"id": 11}
        return {"ingested": 1, "skipped": 0, "failed": 0, "total_chunks": 3, "errors": []}

    original = wb.ingest_files
    wb.ingest_files = _ok_ingest
    try:
        result = writeback.write(_page(), query=QUERY, relevance=0.8)
    finally:
        wb.ingest_files = original

    assert result.written
    assert events == [
        {
            "file_path": result.file_path,
            "document_id": 11,
            "chunk_count": 3,
            "origin": "web_search",
        }
    ]


def test_queue_capacity_and_drain(tmp_path):
    writeback = WebWriteback(tech_docs_dir=tmp_path)
    queue = WebWritebackQueue(writeback, capacity=1)

    assert queue.put({"page": _page(), "query": QUERY, "relevance": 0.8}) is True
    assert queue.put({"page": _page(), "query": QUERY, "relevance": 0.8}) is False
    assert queue.dropped == 1
    assert queue.qsize == 1

    assert queue.drain() == 1
    assert queue.processed == 1
    assert queue.qsize == 0
    assert len(list((tmp_path / "web").glob("*.md"))) == 1


def test_queue_isolates_task_failure():
    traces: list[dict] = []

    class BrokenWriteback:
        trace_recorder = traces.append

        def write(self, page, *, query, relevance):
            raise RuntimeError("boom")

    queue = WebWritebackQueue(BrokenWriteback(), capacity=2)
    assert queue.put({"page": _page(), "query": QUERY, "relevance": 0.8})
    assert queue.drain() == 1
    assert queue.processed == 1
    assert traces and traces[-1]["reason"] == "exception"
