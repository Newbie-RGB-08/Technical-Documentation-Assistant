"""检索策略评分与分级（PRD 3.3.3）。

关键字权重命中 + 长度调节 + 指代惩罚 → 检索置信度分（0~100）→ 检索级别 L0~L3。
领域词表可从知识库元数据（tags / 标题 / 章节路径）自动构建。
"""
from __future__ import annotations

import re

VAGUE_WORDS = ["一些", "怎么弄", "那个东西", "这个", "那个", "它"]
GENERIC_WORDS = ["配置", "安装", "使用", "设置", "部署", "文档", "教程", "怎么"]

# 类别权重（PRD 3.3.3 关键字权重表）
WEIGHT_EXACT = 30   # 精确 API/类名、错误码
WEIGHT_FRAMEWORK = 20  # 框架/工具名
WEIGHT_CONCEPT = 15  # 功能/概念
WEIGHT_VERSION = 10  # 版本/环境限定
WEIGHT_GENERIC = 5   # 通用技术词
PENALTY_VAGUE = -15

CAMEL_SNAKE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*([_.][A-Za-z0-9]+)+$")
CAMEL_CASE_RE = re.compile(r"[a-z][A-Z]")


def _is_exact_api_token(token: str) -> bool:
    """dotted/snake_case 或 CamelCase 视为精确 API/类名。"""
    return bool(CAMEL_SNAKE_RE.match(token) or CAMEL_CASE_RE.search(token))


def decide_level(score: int) -> int:
    """检索置信度分 → 检索级别。"""
    if score >= 80:
        return 3
    if score >= 60:
        return 2
    if score >= 40:
        return 1
    return 0


def build_keyword_weights(
    tags: list[str] | None = None,
    titles: list[str] | None = None,
    heading_paths: list[str] | None = None,
) -> dict[str, int]:
    """从知识库元数据自动构建关键字权重表。

    - tags / 标题整词 → 框架/工具名 +20
    - 标题与章节中的 CamelCase / snake_case / dotted 词 → 精确 API +30
    - 章节路径中的中文词（≥2 字）→ 概念 +15
    """
    weights: dict[str, int] = {}

    def add(term: str, weight: int) -> None:
        term = term.strip()
        if 1 < len(term) <= 64:
            weights.setdefault(term, weight)

    for tag in tags or []:
        add(tag, WEIGHT_FRAMEWORK)
    for title in titles or []:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.]*|[\u4e00-\u9fff]{2,6}", title):
            if _is_exact_api_token(token):
                add(token, WEIGHT_EXACT)
            elif re.match(r"^[\u4e00-\u9fff]", token):
                add(token, WEIGHT_CONCEPT)
            else:
                add(token, WEIGHT_FRAMEWORK)
    for hp in heading_paths or []:
        for part in hp.split(">"):
            part = part.strip()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.]*|[\u4e00-\u9fff]{2,6}", part):
                if _is_exact_api_token(token):
                    add(token, WEIGHT_EXACT)
                else:
                    add(token, WEIGHT_CONCEPT)
            # 英中混排的小节名（如 RRF融合）作为整体概念词保留
            if re.search(r"[A-Za-z]", part) and re.search(r"[\u4e00-\u9fff]", part):
                add(part, WEIGHT_CONCEPT)
    return weights


class RetrievalScorer:
    def __init__(self, keyword_weights: dict[str, int] | None = None):
        self.keyword_weights = keyword_weights or {}

    def score(self, query: str) -> int:
        raw = 0
        lower = query.lower()
        for kw, weight in self.keyword_weights.items():
            if kw.lower() in lower:
                raw += weight

        # 版本/环境限定（每查询计一次）
        if re.search(r"[Vv]?\d+\.\d+", query) or "版本" in query:
            raw += WEIGHT_VERSION
        # 通用技术词（每查询计一次）
        if any(w in query for w in GENERIC_WORDS):
            raw += WEIGHT_GENERIC

        is_vague = any(w in query for w in VAGUE_WORDS)
        # 长度加分按“实质长度”计算：剔除版本号与空白，避免 v0.4.0 这类版本串虚增长度；
        # 模糊问题不给长度加分，避免“这个 X 的问题”被长度分抬高。
        substantive = re.sub(r"[Vv]?\d+(?:\.\d+)+", "", query)
        substantive_len = len(re.sub(r"\s+", "", substantive))
        length_bonus = 10 if not is_vague and 8 <= substantive_len <= 50 else 0
        penalty = PENALTY_VAGUE if is_vague else 0

        return max(0, min(raw + length_bonus + penalty, 100))
