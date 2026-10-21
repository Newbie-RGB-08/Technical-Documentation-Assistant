"""P0：L0~L4 兜底阶梯。"""
from app.core.fallback_ladder import (
    FallbackLevel,
    L2_PREFIX,
    L3_PREFIX,
    L4_NOT_FOUND,
    decide_fallback,
    fallback_message,
)


def test_l0_normal():
    assert decide_fallback(evidence_state="HIT") == FallbackLevel.L0_NORMAL


def test_l1_repaired():
    level = decide_fallback(
        verification_passed=False, repair_attempted=True, repair_passed=True
    )
    assert level == FallbackLevel.L1_REPAIRED


def test_l2_extractive_from_verification_and_llm_failure():
    assert decide_fallback(verification_passed=False) == FallbackLevel.L2_EXTRACTIVE
    assert decide_fallback(llm_failed=True) == FallbackLevel.L2_EXTRACTIVE
    assert decide_fallback(degenerate=True) == FallbackLevel.L2_EXTRACTIVE


def test_l3_partial_and_stale():
    assert decide_fallback(evidence_state="PARTIAL") == FallbackLevel.L3_PARTIAL
    assert decide_fallback(evidence_state="STALE") == FallbackLevel.L3_PARTIAL
    assert decide_fallback(partial=True) == FallbackLevel.L3_PARTIAL


def test_l4_miss_and_sensitive():
    assert decide_fallback(evidence_state="MISS") == FallbackLevel.L4_REFUSED
    assert decide_fallback(sensitive=True) == FallbackLevel.L4_REFUSED


def test_fallback_messages():
    assert fallback_message(FallbackLevel.L2_EXTRACTIVE) == L2_PREFIX
    assert fallback_message(FallbackLevel.L3_PARTIAL) == L3_PREFIX
    assert "建议补充关键词" in fallback_message(
        FallbackLevel.L3_PARTIAL, suggestions=["FastAPI", "CORS"]
    )
    assert fallback_message(FallbackLevel.L4_REFUSED) == L4_NOT_FOUND
    assert fallback_message(FallbackLevel.L0_NORMAL) == ""
