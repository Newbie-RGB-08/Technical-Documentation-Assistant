"""提问信息评分（PRD 3.3.1）。

五维度各 20 分：意图明确度 / 信息完整度 / 领域相关性 / 问题复杂度 / 安全合规。
规则引擎实现，不调用 LLM；领域词表 + 用户近 7 天高频查询辅助相关性判断。
"""
from __future__ import annotations

import re

import jieba

VAGUE_PRONOUNS = ["这个", "那个", "它", "这东西", "那东西"]
JAILBREAK_WORDS = ["忽略之前", "越狱", "开发者模式", "系统提示", "ignore previous"]

VERSION_PATTERN = re.compile(r"[Vv]?\d+\.\d+")
ENV_TERMS = {"windows", "linux", "macos", "mac", "python", "node", "docker", "mysql", "ollama"}
SPECIFIC_TERMS = [
    "API", "api", "函数", "类", "配置", "安装", "部署", "报错", "错误",
    "异常", "使用", "设置", "命令", "参数", "接口",
]

# 用户历史高频词的停用表：疑问/指代/礼貌类泛词不代表领域相关性
_QUESTION_STOPWORDS = {
    "怎么", "如何", "什么", "这个", "那个", "一下", "可以", "请问",
    "告诉", "知道", "为啥", "为什么", "怎样", "哪样", "东西", "问题",
    "解决", "办法", "方法", "谢谢", "感谢", "你好", "您好",
}


class QuestionScorer:
    def __init__(self, domain_terms: set[str] | None = None, session_store=None):
        self.domain_terms = {t.lower() for t in (domain_terms or set())}
        self.session_store = session_store

    # ---- 各维度独立计算，便于单测 ----
    def _intent_score(self, question: str, has_history: bool) -> int:
        """意图明确度（20）：无指代 20 / 有指代可从历史消解 15 / 指代不明 5。"""
        if not any(p in question for p in VAGUE_PRONOUNS):
            return 20
        return 15 if has_history else 5

    def _completeness_score(self, question: str) -> int:
        """信息完整度（20）：版本+具体 20 / 具体 12 / 模糊 4。"""
        lower = question.lower()
        has_version = (
            "版本" in question
            or bool(VERSION_PATTERN.search(question))
            or any(t in lower for t in ENV_TERMS)
        )
        has_specific = any(t in question for t in SPECIFIC_TERMS)
        if has_version and has_specific:
            return 20
        if has_specific:
            return 12
        return 4

    def _relevance_score(self, question: str) -> int:
        """领域相关性（20）：命中领域词≥2 得 20 / 命中 1（含用户历史高频词）得 12 / 0。"""
        lower = question.lower()
        matched = sum(1 for t in self.domain_terms if t and t in lower)
        if matched < 2 and self.session_store is not None:
            user_tags = self._user_recent_tags()
            if any(tag.lower() in lower for tag in user_tags):
                matched += 1
        if matched >= 2:
            return 20
        if matched == 1:
            return 12
        return 0

    def _complexity_score(self, question: str) -> int:
        """问题复杂度（20）：单一 20 / 两个 12 / ≥3 或混乱 4。"""
        marks = question.count("？") + question.count("?")
        if marks <= 1:
            return 20
        if marks == 2:
            return 12
        return 4

    def _safety_score(self, question: str) -> int:
        """安全合规（20）：含越狱词 0 / 正常 20。"""
        lower = question.lower()
        return 0 if any(w in question or w in lower for w in JAILBREAK_WORDS) else 20

    def _user_recent_tags(self, days: int = 7, top_n: int = 8) -> list[str]:
        try:
            questions = self.session_store.recent_questions(days=days)
        except Exception:  # noqa: BLE001 - 存储不可用时评分降级继续
            return []
        freq: dict[str, int] = {}
        for q in questions:
            for token in jieba.cut(q):
                token = token.strip().lower()
                if len(token) >= 2 and token not in _QUESTION_STOPWORDS:
                    freq[token] = freq.get(token, 0) + 1
        # 高频词需真实重复出现，避免单次提问的泛词被当领域相关性
        ranked = sorted(
            ((t, n) for t, n in freq.items() if n >= 2),
            key=lambda x: x[1], reverse=True,
        )[:top_n]
        return [t for t, _ in ranked]

    # ---- 总分 ----
    def score(self, question: str, history: list[dict] | None = None) -> int:
        total = (
            self._intent_score(question, bool(history))
            + self._completeness_score(question)
            + self._relevance_score(question)
            + self._complexity_score(question)
            + self._safety_score(question)
        )
        return max(0, min(total, 100))
