"""P0：证据快照与本地可回答性判定。"""
from datetime import datetime, timedelta, timezone

from app.core.evidence import (
    Answerability,
    TRUST_LOCAL_HIGH,
    TRUST_WEB_MEDIUM,
    build_evidence_pack,
    entity_coverage,
    extract_entities,
    fit_context_chunks,
    judge_answerability,
)


def _chunk(text, meta=None, rerank=None, score=0.8):
    chunk = {"text": text, "metadata": meta or {}, "score": score}
    if rerank is not None:
        chunk["rerank_score"] = rerank
    return chunk


def test_build_evidence_pack_fixed_numbering_and_trust():
    chunks = [
        _chunk("本地内容", {"file_path": "data/notes/a.md"}, rerank=0.8),
        _chunk(
            "网页内容",
            {"file_path": "data/tech_docs/web/x.md", "origin": "web_search",
             "source_url": "https://example.com/x", "retrieved_at": "2026-10-01T00:00:00+08:00"},
            rerank=0.7,
        ),
    ]
    pack = build_evidence_pack(chunks)
    assert pack.indices == [1, 2]
    assert pack.get(1).trust_level == TRUST_LOCAL_HIGH
    assert pack.get(2).trust_level == TRUST_WEB_MEDIUM
    assert pack.get(2).origin == "web_search"
    assert pack.get(2).retrieved_at == "2026-10-01T00:00:00+08:00"
    assert pack.text(1) == "本地内容"
    assert pack.get(99) is None


def test_extract_entities_and_coverage():
    question = "TimeoutError 和 v0.4.0 在 localhost:8080 下怎么处理？"
    entities = extract_entities(question)
    assert "TimeoutError" in entities
    assert "v0.4.0" in entities
    assert "localhost:8080" in entities

    chunks = [_chunk("TimeoutError 表示超时，请检查服务。")]
    assert entity_coverage(question, chunks) == 1 / 3
    # 无精确实体的概念题不因覆盖率被惩罚
    assert entity_coverage("什么是向量检索？", chunks) == 1.0


def test_judge_hit_partial_miss():
    hit_chunks = [_chunk("FastAPI 通过 CORSMiddleware 配置 CORS。", rerank=0.90)]
    assert judge_answerability(hit_chunks, "FastAPI 如何配置 CORS？") == Answerability.HIT

    partial_chunks = [_chunk("FastAPI 相关内容。", rerank=0.35)]
    assert judge_answerability(partial_chunks, "FastAPI 如何配置 CORS？") == Answerability.PARTIAL

    miss_chunks = [_chunk("FastAPI 相关内容。", rerank=0.10)]
    assert judge_answerability(miss_chunks, "FastAPI 如何配置 CORS？") == Answerability.MISS
    assert judge_answerability([], "FastAPI 如何配置 CORS？") == Answerability.MISS


def test_judge_stale_for_recency_question():
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")
    chunks = [
        _chunk(
            "ChromaDB 最新版本是 1.0。",
            {"file_path": "data/notes/a.md", "updated_at": old},
            rerank=0.9,
        )
    ]
    assert judge_answerability(chunks, "ChromaDB 最新版本是什么？") == Answerability.STALE
    # 不带时效词时仍是 HIT
    assert judge_answerability(chunks, "ChromaDB 怎么用？") == Answerability.HIT


def test_fit_context_respects_budget_and_order():
    chunks = [_chunk("a" * 100), _chunk("b" * 100), _chunk("c" * 100)]
    fitted = fit_context_chunks(chunks, max_chars=500)
    assert fitted
    assert len(fitted) < len(chunks)
    assert fitted[0]["text"] == "a" * 100
    assert sum(len(c["text"]) + 120 for c in fitted) <= 500


def test_fit_context_truncates_long_single_chunk():
    chunks = [_chunk("x" * 1000)]
    fitted = fit_context_chunks(chunks, max_chars=300)
    assert len(fitted) == 1
    assert len(fitted[0]["text"]) == 180
