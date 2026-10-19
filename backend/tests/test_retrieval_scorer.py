"""阶段 3 测试：检索策略评分与分级（PRD 3.3.3）。"""
from app.core.retrieval_scorer import (
    RetrievalScorer,
    build_keyword_weights,
    decide_level,
)


def test_decide_level_boundaries():
    assert decide_level(39) == 0
    assert decide_level(40) == 1
    assert decide_level(59) == 1
    assert decide_level(60) == 2
    assert decide_level(79) == 2
    assert decide_level(80) == 3
    assert decide_level(100) == 3
    assert decide_level(0) == 0


def test_score_with_keyword_weights():
    scorer = RetrievalScorer({"BM25Okapi": 30, "ChromaDB": 20, "混合检索": 15})
    # 命中 30+20+15=65，无长度加分（query 长度 8~50 内则 +10）
    q = "ChromaDB 的 BM25Okapi 和混合检索怎么用"
    assert 8 <= len(q) <= 50
    assert scorer.score(q) == 65 + 5 + 10  # 通用词"怎么" +5，长度 +10


def test_vague_penalty_and_clamp():
    scorer = RetrievalScorer({"ChromaDB": 20})
    # "这个" vague -15；20-15=5
    assert scorer.score("这个 ChromaDB 的问题") == 5
    # 全模糊词无命中 → clamp 到 0
    assert scorer.score("那个东西怎么弄") == 0


def test_version_bonus():
    scorer = RetrievalScorer({})
    assert scorer.score("Python 3.11 版本兼容性如何") == 10 + 10  # 版本 +10 长度 +10
    assert scorer.score("v0.4.0 的更新内容") == 10  # 长度不足 8 无加分


def test_build_keyword_weights_from_metadata():
    weights = build_keyword_weights(
        tags=["chroma", "rag"],
        titles=["PersistentClient 持久化存储", "混合检索策略"],
        heading_paths=["检索策略 > RRF融合"],
    )
    assert weights["chroma"] == 20
    assert weights["rag"] == 20
    assert weights["PersistentClient"] == 30  # CamelCase → 精确 API
    assert weights["持久化存储"] == 15
    assert weights["RRF融合"] == 30 or weights["RRF融合"] == 15
    assert "检索策略" in weights


def test_score_upper_bound():
    scorer = RetrievalScorer({f"kw{i}": 30 for i in range(10)})
    assert scorer.score("kw0 kw1 kw2 kw3 kw4 kw5 kw6 kw7 kw8 kw9 全部命中权重") == 100
