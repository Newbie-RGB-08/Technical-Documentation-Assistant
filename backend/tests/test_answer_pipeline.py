"""P1：严格管道 / 修复 / 摘录 / 通用流式守卫。"""
from app.core.evidence import build_evidence_pack
from app.core.fallback_ladder import FallbackLevel, L2_PREFIX
from app.services.answer_pipeline import (
    classify_specificity,
    guard_general_answer,
    run_general_stream,
    run_strict_pipeline,
)


class FakeGateway:
    def __init__(self, responses=None, stream_tokens=None, fail=False):
        self.responses = list(responses or [])
        self.stream_tokens = list(stream_tokens or [])
        self.fail = fail
        self.calls: list[dict] = []

    async def chat(self, messages, task_type="generate", stream=False, model_id=None, **kwargs):
        self.calls.append({"messages": messages, "stream": stream, "task_type": task_type})
        if self.fail:
            raise RuntimeError("all providers failed")
        if stream:

            async def gen():
                for token in self.stream_tokens:
                    yield token

            return gen()
        return self.responses.pop(0)


def _pack():
    return build_evidence_pack(
        [
            {
                "text": "ChromaDB 使用 PersistentClient 持久化数据。",
                "metadata": {"file_path": "data/notes/a.md", "title": "Chroma 笔记"},
                "rerank_score": 0.9,
            },
            {
                "text": "RRF 融合向量与 BM25 两路结果。",
                "metadata": {"file_path": "data/notes/b.md", "title": "检索笔记"},
                "rerank_score": 0.8,
            },
        ]
    )


async def test_strict_pipeline_verified_answer():
    gateway = FakeGateway(["ChromaDB 使用 PersistentClient 持久化数据。[来源1]"])
    result = await run_strict_pipeline(gateway, "ChromaDB 怎么持久化？", _pack())
    assert result.status == "verified"
    assert result.verified is True
    assert result.fallback_level == int(FallbackLevel.L0_NORMAL)
    assert result.used_indices == [1]
    assert len(gateway.calls) == 1


async def test_strict_pipeline_repairs_once():
    # 首答故意缺引用但不含精确实体 → unsupported（非 severe）→ 允许修复一次
    gateway = FakeGateway(
        [
            "这个工具在使用时需要注意几个关键配置。",
            "ChromaDB 使用 PersistentClient 持久化数据。[来源1]",
        ]
    )
    result = await run_strict_pipeline(gateway, "ChromaDB 怎么持久化？", _pack())
    assert result.status == "verified"
    assert result.fallback_level == int(FallbackLevel.L1_REPAIRED)
    assert len(gateway.calls) == 2
    assert "校验发现以下问题" in gateway.calls[1]["messages"][1]["content"]


async def test_strict_pipeline_severe_entity_skips_repair():
    gateway = FakeGateway(["ChromaDB 9.9.9 使用 PersistentClient 持久化数据。[来源1]"])
    result = await run_strict_pipeline(gateway, "ChromaDB 怎么持久化？", _pack())
    assert result.status == "extractive"
    assert result.fallback_level == int(FallbackLevel.L2_EXTRACTIVE)
    assert len(gateway.calls) == 1
    assert result.text.startswith(L2_PREFIX)
    assert "[来源1]" in result.text


async def test_strict_pipeline_repair_fails_to_extractive():
    # 首答缺引用但不含精确实体 → 允许修复；修复后仍未通过 → 摘录兜底
    gateway = FakeGateway(
        [
            "这个工具在使用时需要注意几个关键配置。",
            "修复后的内容还是没有引用。",  # 修复后仍缺引用且不含实体 → 还是 unsupported
        ]
    )
    result = await run_strict_pipeline(gateway, "ChromaDB 怎么持久化？", _pack())
    assert result.status == "extractive"
    assert result.fallback_level == int(FallbackLevel.L2_EXTRACTIVE)
    assert len(gateway.calls) == 2


async def test_strict_pipeline_llm_failure_gives_extractive():
    gateway = FakeGateway(fail=True)
    result = await run_strict_pipeline(gateway, "ChromaDB 怎么持久化？", _pack())
    assert result.status == "extractive"
    assert "[来源1]" in result.text
    assert "模型服务" not in result.text


async def test_strict_pipeline_conflict_disclosure():
    gateway = FakeGateway(["当前版本是 1.0。[来源1]"])
    pack = build_evidence_pack(
        [
            {"text": "当前版本 1.0 支持该功能。", "metadata": {"file_path": "a.md"}},
            {"text": "当前版本 2.0 支持该功能。", "metadata": {"file_path": "b.md"}},
        ]
    )
    result = await run_strict_pipeline(gateway, "当前版本是什么？", pack)
    assert result.status == "partial"
    assert result.conflicts
    assert "资料存在冲突" in result.text
    assert len(gateway.calls) == 1


def test_classify_specificity_boundaries():
    assert classify_specificity("什么是 RAG？") == "concept"
    assert classify_specificity("向量检索和关键词检索的区别是什么？") == "concept"
    assert classify_specificity("FastAPI 如何配置 CORS？") == "specific"
    assert classify_specificity("Uvicorn 启动报错怎么排查？") == "specific"
    assert classify_specificity("v0.4.0 还适用吗？") == "specific"


def test_guard_general_answer_flags_pseudo_citation_and_specifics():
    flagged, reasons = guard_general_answer("根据资料显示，使用 [来源1] 即可。")
    assert flagged is True
    assert any("伪引用" in r for r in reasons)

    flagged, reasons = guard_general_answer("默认端口是 localhost:8080，版本 v1.2.3。")
    assert flagged is True
    assert any("具体" in r for r in reasons)

    flagged, _ = guard_general_answer("向量检索通过嵌入向量计算相似度，与关键词检索互补。")
    assert flagged is False


async def test_general_stream_disclaimer_and_unverified_status():
    gateway = FakeGateway(stream_tokens=["向量检索", "通过相似度匹配。"])
    events = [event async for event in run_general_stream(gateway, "什么是向量检索？")]
    assert events[0]["event"] == "token"
    assert events[0]["text"].startswith("以下回答未基于本地知识库")
    result = events[-1]["result"]
    assert result.status == "unverified_general"
    assert "以下回答未基于本地知识库" in result.text


async def test_general_stream_flags_fake_citation():
    gateway = FakeGateway(stream_tokens=["根据资料显示，答案是 [来源1]。"])
    events = [event async for event in run_general_stream(gateway, "什么是向量检索？")]
    result = events[-1]["result"]
    assert result.status == "unverified_general_flagged"
    assert "更正提示" in result.text


async def test_general_stream_llm_failure_no_exception():
    gateway = FakeGateway(fail=True)
    events = [event async for event in run_general_stream(gateway, "什么是向量检索？")]
    result = events[-1]["result"]
    assert result.status == "refused"
    assert "模型服务暂不可用" in result.text
