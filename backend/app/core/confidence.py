"""结果置信度计算与兜底策略（PRD 3.3.4）。"""
from __future__ import annotations

import re

CONFIDENCE_HIGH = 0.6
CONFIDENCE_LOW = 0.3

FALLBACK_REFUSE = "抱歉，我无法处理该请求。"
FALLBACK_NOT_FOUND = "未在现有文档中找到相关内容，请补充关键词或换一种问法。"
DISCLAIMER = "以下回答未基于本地知识库，仅供参考。"
INCOMPLETE_NOTICE = "以下回答可能不完整，请结合来源自行判断。"


def compute_confidence(chunks: list[dict], level: int) -> float:
    """按检索级别计算结果置信度（0~1）。

    L3：rerank 最高分；L2：RRF 分经验归一化（max/0.05）；L1：向量相似度最高分。
    """
    if not chunks:
        return 0.0
    if level == 3:
        return float(max(c.get("rerank_score", 0.0) for c in chunks))
    if level == 2:
        max_rrf = max(c.get("score", 0.0) for c in chunks)
        normalized = min(max_rrf / 0.05, 1.0)
        # BM25 支路为空时 RRF 分最高仅约 1/(k+1)，归一化会系统性低估；
        # 此时用保留的向量相似度兜底，避免高质量向量结果被误判为中低置信。
        vector_scores = [c["vector_score"] for c in chunks if c.get("vector_score") is not None]
        if vector_scores:
            return max(normalized, float(max(vector_scores)))
        return normalized
    return float(max(c.get("score", 0.0) for c in chunks))


def classify_fallback(question: str) -> str:
    """兜底问题分流：sensitive 拒答 / kb_related 未找到 / general 声明式自答。

    kb_related 判定：问题命中精确技术词（API/框架形态）但知识库没有 —— 视为强相关。
    """
    from app.core.retrieval_scorer import CAMEL_CASE_RE, CAMEL_SNAKE_RE

    jailbreak = ["忽略之前", "越狱", "开发者模式", "系统提示", "ignore previous"]
    if any(w in question or w in question.lower() for w in jailbreak):
        return "sensitive"
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.]{2,}", question)
    # 仅把精确 API/类名形态（CamelCase、dotted/snake）判为强知识库相关；
    # REST/API 这类全大写通用缩写仍按概念性问题处理。
    if any(CAMEL_SNAKE_RE.match(t) or CAMEL_CASE_RE.search(t) for t in tokens):
        return "kb_related"
    return "general"


def build_fallback_preamble(kind: str) -> str:
    """兜底回答的前缀文案（LLM 生成部分由网关负责，此处只管框架文案）。"""
    if kind == "sensitive":
        return FALLBACK_REFUSE
    if kind == "kb_related":
        return FALLBACK_NOT_FOUND
    return DISCLAIMER
