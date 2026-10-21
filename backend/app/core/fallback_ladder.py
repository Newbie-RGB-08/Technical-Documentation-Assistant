"""L0~L4 兜底阶梯决策与文案（PRD 附录 E.8）。"""
from __future__ import annotations

from enum import IntEnum


class FallbackLevel(IntEnum):
    L0_NORMAL = 0
    L1_REPAIRED = 1
    L2_EXTRACTIVE = 2
    L3_PARTIAL = 3
    L4_REFUSED = 4


L2_PREFIX = "根据现有资料，只能确认以下内容："
L3_PREFIX = "现有资料只覆盖了部分内容，以下为可确认部分："
L4_NOT_FOUND = "未找到可靠依据，无法给出经过验证的答案。请补充关键词或核对官方文档。"


def decide_fallback(
    *,
    sensitive: bool = False,
    evidence_state: str = "HIT",
    verification_passed: bool = True,
    repair_attempted: bool = False,
    repair_passed: bool = False,
    llm_failed: bool = False,
    degenerate: bool = False,
    partial: bool = False,
) -> FallbackLevel:
    """按故障与校验结果选择兜底级别（L0 正常优先）。"""
    if sensitive:
        return FallbackLevel.L4_REFUSED
    if llm_failed or degenerate:
        return FallbackLevel.L2_EXTRACTIVE
    if not verification_passed:
        if repair_attempted and repair_passed:
            return FallbackLevel.L1_REPAIRED
        return FallbackLevel.L2_EXTRACTIVE
    if evidence_state == "MISS":
        return FallbackLevel.L4_REFUSED
    if evidence_state in ("PARTIAL", "STALE") or partial:
        return FallbackLevel.L3_PARTIAL
    return FallbackLevel.L0_NORMAL


def fallback_message(
    level: FallbackLevel,
    *,
    suggestions: list[str] | None = None,
) -> str:
    """返回兜底回答的固定文案；摘录正文由 answer_pipeline 追加。"""
    if level == FallbackLevel.L2_EXTRACTIVE:
        return L2_PREFIX
    if level == FallbackLevel.L3_PARTIAL:
        text = L3_PREFIX
        if suggestions:
            text += "\n\n建议补充关键词：" + "、".join(suggestions)
        return text
    if level == FallbackLevel.L4_REFUSED:
        return L4_NOT_FOUND
    return ""
