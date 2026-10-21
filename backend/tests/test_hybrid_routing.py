"""P2 测试：混合答案模式在 chat_flow 中的分流（PRD E.4 / E.5 / E.7）。

覆盖：HIT/PARTIAL/STALE 走严格管道；MISS + 宽泛概念走通用流式；
MISS + 具体问题明确拒绝；复杂度 meta/status 事件完整。
"""
import pytest

from app.services import rag_service
from app.services.session_store import SessionStore


class FakeQuestionScorer:
    def __init__(self, score: int = 85):
        self.value = score

    def score(self, question, history=None):
        return self.value


class FakeRetrievalScorer:
    def __init__(self, score: int = 90, weights: dict | None = None):
        self.value = score
        self.keyword_weights = weights or {}

    def score(self, query):
        return self.value


class FakeRetriever:
    def __init__(self, chunks: list[dict], level: int = 3, confidence: float = 0.9):
        self.chunks = chunks
        self.level = level
        self.confidence = confidence

    def retrieve_with_fallback(self, query, level=3, top_k=5):
        return list(self.chunks), self.level, self.confidence


class FakeGateway:
    current_model = "fake-model"
    current_provider = "fake"

    def __init__(self, answer: str = "", tokens: list[str] | None = None):
        self.answer = answer
        self.tokens = tokens or []
        self.generate_calls = 0
        self.stream_calls = 0

    async def chat(self, messages, task_type="generate", stream=False, model_id=None, **kwargs):
        if stream:
            self.stream_calls += 1

            async def _gen():
                for token in self.tokens:
                    yield token

            return _gen()
        self.generate_calls += 1
        return self.answer


class FakeState:
    def __init__(self, tmp_path, chunks, *, q_score=85, r_score=90,
                 gateway=None, weights=None):
        self.session_store = SessionStore(tmp_path / "sessions.db")
        self.question_scorer = FakeQuestionScorer(q_score)
        self.retrieval_scorer = FakeRetrievalScorer(r_score, weights)
        self.retriever = FakeRetriever(chunks)
        self.gateway = gateway or FakeGateway()


async def _run(state, question="ChromaDB 的 PersistentClient 如何启动？"):
    events = []
    async for event in rag_service.chat_flow(state, question, "s1"):
        events.append(event)
    return events


def _text(events) -> str:
    return "".join(data.get("text", "") for kind, data in events if kind == "content")


def _first(events, kind):
    for event_kind, data in events:
        if event_kind == kind:
            return data
    return None


def _last(events, kind):
    found = None
    for event_kind, data in events:
        if event_kind == kind:
            found = data
    return found


_HIT_CHUNKS = [
    {
        "id": "c1",
        "text": "ChromaDB 的 PersistentClient 通过持久化路径启动。",
        "metadata": {
            "file_path": "data/notes/chroma.md",
            "title": "Chroma 入门",
            "heading_path": "/ 持久化",
            "source_type": "personal_note",
            "source_url": "",
        },
        "rerank_score": 0.92,
    }
]


@pytest.mark.asyncio
async def test_hit_goes_through_strict_pipeline(tmp_path):
    gateway = FakeGateway(answer="ChromaDB 的 PersistentClient 通过持久化路径启动。[来源1]")
    state = FakeState(tmp_path, _HIT_CHUNKS, gateway=gateway)

    events = await _run(state)

    stages = [d["stage"] for kind, d in events if kind == "status"]
    assert stages[:2] == ["scoring", "local_retrieval"]
    assert "generating" in stages and "verifying" in stages

    answer_meta = _last(events, "meta")
    assert answer_meta["complexity"] == "simple"
    assert answer_meta["route"] == "fallback"
    assert answer_meta["answer_mode"] == "hybrid"

    sources = _first(events, "sources")
    assert sources and sources[0]["index"] == 1

    assert "PersistentClient" in _text(events)
    done = _last(events, "done")
    assert done["answer_status"] == "verified"
    assert done["verified"] is True
    assert done["kb_written"] is False
    assert done["fallback_level"] == 0
    assert gateway.generate_calls == 1
    assert gateway.stream_calls == 0


@pytest.mark.asyncio
async def test_partial_adds_incomplete_notice(tmp_path):
    chunks = [dict(_HIT_CHUNKS[0], rerank_score=0.35)]  # >=0.30 且实体覆盖 → PARTIAL
    gateway = FakeGateway(answer="ChromaDB 的 PersistentClient 通过持久化路径启动。[来源1]")
    state = FakeState(tmp_path, chunks, gateway=gateway)

    events = await _run(state)

    status = [d for kind, d in events if kind == "status" and d.get("stage") == "generating"]
    assert status and status[0]["answerability"] == "PARTIAL"
    assert _text(events).startswith("以下回答可能不完整")
    assert _last(events, "done")["answer_status"] == "verified"


@pytest.mark.asyncio
async def test_stale_adds_recency_notice(tmp_path):
    chunks = [
        dict(
            _HIT_CHUNKS[0],
            metadata=dict(_HIT_CHUNKS[0]["metadata"], updated_at="2020-01-01T00:00:00+00:00"),
        )
    ]
    gateway = FakeGateway(answer="ChromaDB 的 PersistentClient 通过持久化路径启动。[来源1]")
    state = FakeState(tmp_path, chunks, gateway=gateway)

    events = await _run(state, "最新的 ChromaDB PersistentClient 如何启动？")

    status = [d for kind, d in events if kind == "status" and d.get("stage") == "generating"]
    assert status and status[0]["answerability"] == "STALE"
    assert "超过时效" in _text(events)


@pytest.mark.asyncio
async def test_miss_concept_uses_general_stream_with_disclaimer(tmp_path):
    gateway = FakeGateway(tokens=["向量数据库", "是存储向量的系统。"])
    state = FakeState(tmp_path, [], gateway=gateway)

    events = await _run(state, "什么是向量数据库？")

    stages = [d["stage"] for kind, d in events if kind == "status"]
    assert "general_streaming" in stages
    assert gateway.stream_calls == 1
    assert gateway.generate_calls == 0

    text = _text(events)
    assert text.startswith("以下回答未基于本地知识库")
    assert "向量数据库" in text

    done = _last(events, "done")
    assert done["answer_status"] == "unverified_general"
    assert done["verified"] is False
    assert done["kb_written"] is False

    history = state.session_store.get_history("s1")
    assert [m["role"] for m in history] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_miss_specific_refuses_without_llm(tmp_path):
    gateway = FakeGateway(tokens=["不应该出现"])
    state = FakeState(tmp_path, [], gateway=gateway)

    events = await _run(state, "FastAPI lifespan 在 v0.115.0 中默认值是什么？")

    text = _text(events)
    assert "未找到可靠依据" in text
    assert "不应该出现" not in text
    assert gateway.generate_calls == 0 and gateway.stream_calls == 0

    done = _last(events, "done")
    assert done["answer_status"] == "refused"
    assert done["fallback_level"] == 4


@pytest.mark.asyncio
async def test_complex_question_routes_parallel(tmp_path):
    question = (
        "我们目前在评估 ChromaDB 和 BM25 的区别以及 RRF 融合的最佳实践，"
        "性能、成本、运维都要考虑，2026 年最新版本如何选择？还有如何迁移？"
    )
    gateway = FakeGateway(answer="ChromaDB 与 BM25 各有侧重。[来源1]")
    state = FakeState(tmp_path, _HIT_CHUNKS, gateway=gateway)

    events = await _run(state, question)

    meta = _last(events, "meta")
    assert meta["complexity"] == "complex"
    assert meta["route"] == "parallel"
    assert meta["complexity_score"] >= 60

    status = [d for kind, d in events if kind == "status" and d.get("stage") == "local_retrieval"]
    assert status and status[0]["level"] == 3
