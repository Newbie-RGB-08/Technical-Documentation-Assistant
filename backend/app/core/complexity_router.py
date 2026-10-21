"""问题复杂度评分与检索路由（PRD 附录 E.3）。

规则引擎，不调用 LLM。分数 0~100 → simple / medium / complex。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SUBQUESTION_WORDS = ("以及", "还有", "分别", "同时")
MULTIHOP_WORDS = (
    "对比", "区别", "差异", "选型", "迁移", "为什么", "为啥", "原理",
    "排查", "优化", "最佳实践", "分析", "如何选择",
)
DIMENSION_WORDS = ("性能", "成本", "运维", "安全", "稳定性", "扩展", "兼容", "可维护")
RECENCY_WORDS = ("最新", "现在", "今年", "当前", "目前", "latest")
ERROR_WORDS = ("Traceback", "Exception", "报错", "错误", "异常")
PRONOUNS = ("这个", "那个", "它")
YEAR_RE = re.compile(r"\b20\d{2}\b")
CODE_CONTEXT_RE = re.compile(r"```|Traceback|File \"")
FRAMEWORK_WEIGHT = 20


@dataclass(frozen=True)
class RouteDecision:
    complexity: str
    local_level: int
    web_strategy: str  # none / fallback / parallel


def classify_complexity(score: int) -> str:
    if score >= 60:
        return "complex"
    if score >= 30:
        return "medium"
    return "simple"


def _subquestion_count(question: str) -> int:
    marks = question.count("？") + question.count("?")
    conjunctions = sum(question.count(word) for word in SUBQUESTION_WORDS)
    return max(marks, 1 + conjunctions)


def _matched_keywords(question: str, keyword_weights: dict[str, int] | None) -> list[str]:
    lower = question.lower()
    matched = []
    for keyword in keyword_weights or {}:
        if keyword and keyword.lower() in lower:
            matched.append(keyword)
    # 去掉被更长关键词包含的重复命中（如 FastAPI 与 Fast）
    matched.sort(key=len, reverse=True)
    kept: list[str] = []
    for keyword in matched:
        if not any(keyword.lower() in other.lower() for other in kept):
            kept.append(keyword)
    return kept


def _has_unresolved_pronoun(question: str, history: list[dict] | None) -> bool:
    if not any(word in question for word in PRONOUNS):
        return False
    recent = (history or [])[-6:]
    if not recent:
        return True
    text = " ".join(str(item.get("content") or item.get("user") or "") for item in recent)
    # 历史里没有任何技术词/英文实体，视为无法消解指代
    return not bool(re.search(r"[A-Za-z][A-Za-z0-9_.-]{2,}", text))


def score_complexity(
    question: str,
    keyword_weights: dict[str, int] | None = None,
    history: list[dict] | None = None,
) -> int:
    """E.3.2 九类特征加权，clamp 到 0~100。"""
    score = 0

    sub_count = _subquestion_count(question)
    if sub_count >= 3:
        score += 30
    elif sub_count == 2:
        score += 20

    matched = _matched_keywords(question, keyword_weights)
    entity_count = len(matched)
    if entity_count >= 3:
        score += 20
    elif entity_count == 2:
        score += 10

    if any(word in question for word in MULTIHOP_WORDS):
        score += 25

    dimensions = list(dict.fromkeys(word for word in DIMENSION_WORDS if word in question))
    if len(dimensions) >= 2:
        score += 15

    compact_len = len(re.sub(r"\s+", "", question))
    if compact_len > 120:
        score += 15
    elif compact_len > 60:
        score += 10

    if any(word in question for word in ERROR_WORDS) and CODE_CONTEXT_RE.search(question):
        score += 20

    if _has_unresolved_pronoun(question, history):
        score += 15

    if any(word in question for word in RECENCY_WORDS) or YEAR_RE.search(question):
        score += 10

    framework_hits = [
        keyword
        for keyword in matched
        if (keyword_weights or {}).get(keyword) == FRAMEWORK_WEIGHT
    ]
    if len(framework_hits) >= 2:
        score += 10

    return max(0, min(score, 100))


def route_for_complexity(complexity: str, r_score: int) -> RouteDecision:
    """复杂度 + 检索评分 → 本地级别与线上策略（PRD E.3.3）。"""
    if complexity == "simple":
        local_level = 3 if r_score >= 80 else 2
        return RouteDecision("simple", local_level, "fallback")
    if complexity == "medium":
        return RouteDecision("medium", 3, "fallback")
    return RouteDecision("complex", 3, "parallel")
