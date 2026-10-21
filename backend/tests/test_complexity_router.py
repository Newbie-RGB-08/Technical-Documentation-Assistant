"""P2 测试：复杂度评分与检索路由（PRD E.3）。"""
from app.core.complexity_router import (
    classify_complexity,
    route_for_complexity,
    score_complexity,
)


def test_classify_boundaries():
    assert classify_complexity(0) == "simple"
    assert classify_complexity(29) == "simple"
    assert classify_complexity(30) == "medium"
    assert classify_complexity(59) == "medium"
    assert classify_complexity(60) == "complex"
    assert classify_complexity(100) == "complex"


def test_simple_concept_question_stays_simple():
    score = score_complexity("什么是向量数据库")
    assert score < 30
    assert classify_complexity(score) == "simple"


def test_multi_subquestion_adds_weight():
    question = "如何配置 ChromaDB？还有如何配置 BM25？以及如何做 RRF 融合？"
    score = score_complexity(question)
    assert score >= 30


def test_comparison_and_frameworks_raise_to_medium():
    weights = {"ChromaDB": 20, "BM25": 20}
    score = score_complexity("ChromaDB 和 BM25 的区别是什么", weights)
    assert score >= 30
    assert classify_complexity(score) == "medium"


def test_multi_keyword_frameworks_bonus():
    weights = {"ChromaDB": 20, "BM25": 20, "RRF": 30}
    with_entities = score_complexity("ChromaDB BM25 RRF 是什么", weights)
    without_entities = score_complexity("这个系统是什么", {})
    assert with_entities > without_entities


def test_long_question_with_dimensions_and_recency_is_complex():
    question = (
        "我们目前在评估 ChromaDB 和 BM25 的区别以及 RRF 融合的最佳实践，"
        "性能、成本、运维都要考虑，2026 年最新版本如何选择？还有如何迁移？"
    )
    score = score_complexity(question, {})
    assert score >= 60
    assert classify_complexity(score) == "complex"


def test_error_with_traceback_adds_weight():
    plain = "报错怎么处理"
    with_code = "报错怎么处理\n```\nTraceback (most recent call last):\n```"
    assert score_complexity(with_code) - score_complexity(plain) >= 20


def test_unresolved_pronoun_without_history():
    assert score_complexity("这个怎么配置") >= 15
    assert score_complexity("这个怎么配置") > score_complexity("ChromaDB 怎么配置")


def test_pronoun_resolved_by_history():
    history = [{"role": "user", "content": "ChromaDB 的 PersistentClient 是什么？"}]
    assert score_complexity("它怎么启动", history=history) < score_complexity("它怎么启动")


def test_keyword_dedupe_longer_wins():
    weights = {"FastAPI": 20, "Fast": 20}
    # 只计 FastAPI 一次，不应因为子串 Fast 重复加分
    score = score_complexity("FastAPI 部署", weights)
    assert score < 30


def test_route_simple_uses_level_by_r_score():
    low = route_for_complexity("simple", 50)
    assert low.local_level == 2
    assert low.web_strategy == "fallback"

    high = route_for_complexity("simple", 85)
    assert high.local_level == 3
    assert high.web_strategy == "fallback"


def test_route_medium_and_complex():
    medium = route_for_complexity("medium", 10)
    assert medium.local_level == 3
    assert medium.web_strategy == "fallback"

    complex_route = route_for_complexity("complex", 10)
    assert complex_route.local_level == 3
    assert complex_route.web_strategy == "parallel"
