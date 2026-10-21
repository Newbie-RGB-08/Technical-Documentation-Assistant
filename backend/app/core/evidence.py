"""证据快照与本地可回答性判定（PRD 附录 E.5）。

证据快照固定编号 1..N，编号与送入 LLM 的 context 完全一致；
不改变 V1.1~V1.3 的来源卡片合并与 `[来源N]` 规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.core.retrieval_scorer import CAMEL_CASE_RE, CAMEL_SNAKE_RE

TRUST_LOCAL_HIGH = "local_high"
TRUST_WEB_MEDIUM = "web_medium"

# 每个片段在 build_context 中的元数据开销（编号/文件/章节/来源行）
CONTEXT_META_OVERHEAD = 120

RECENCY_TERMS = ("最新", "现在", "今年", "当前", "目前", "latest", "版本")
VERSION_RE = re.compile(r"(?<![\w.])[vV]?\d+(?:\.\d+){1,3}(?![\w.])")
PORT_RE = re.compile(r"(?:localhost|127\.0\.0\.1)?:\d{2,5}\b")
FLAG_RE = re.compile(r"--[A-Za-z][A-Za-z0-9_-]*")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")


class Answerability(str, Enum):
    HIT = "HIT"
    PARTIAL = "PARTIAL"
    MISS = "MISS"
    STALE = "STALE"


@dataclass
class EvidenceItem:
    index: int
    text: str
    metadata: dict = field(default_factory=dict)
    score: float = 0.0
    trust_level: str = TRUST_LOCAL_HIGH
    origin: str = "local"
    retrieved_at: str | None = None

    @property
    def source_url(self) -> str:
        return str(self.metadata.get("source_url") or "")


@dataclass
class EvidencePack:
    items: list[EvidenceItem]

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def max_index(self) -> int:
        return len(self.items)

    @property
    def indices(self) -> list[int]:
        return [item.index for item in self.items]

    def get(self, index: int) -> EvidenceItem | None:
        for item in self.items:
            if item.index == index:
                return item
        return None

    def text(self, index: int) -> str:
        item = self.get(index)
        return item.text if item else ""

    def chunks(self) -> list[dict]:
        return [
            {
                "id": f"evidence-{item.index}",
                "text": item.text,
                "metadata": item.metadata,
                "score": item.score,
            }
            for item in self.items
        ]


def _is_web(meta: dict) -> bool:
    return str(meta.get("origin") or "") == "web_search" or bool(
        str(meta.get("source_url") or "").strip()
    )


def build_evidence_pack(chunks: list[dict]) -> EvidencePack:
    """按 chunk 顺序生成固定编号 1..N 的证据快照。"""
    items: list[EvidenceItem] = []
    for i, chunk in enumerate(chunks, start=1):
        meta = chunk.get("metadata") or {}
        web = _is_web(meta)
        items.append(
            EvidenceItem(
                index=i,
                text=str(chunk.get("text") or ""),
                metadata=meta,
                score=float(chunk.get("rerank_score", chunk.get("score", 0.0)) or 0.0),
                trust_level=TRUST_WEB_MEDIUM if web else TRUST_LOCAL_HIGH,
                origin="web_search" if web else "local",
                retrieved_at=str(meta.get("retrieved_at") or "") or None,
            )
        )
    return EvidencePack(items=items)


def extract_entities(question: str) -> list[str]:
    """提取问题中的精确技术实体：API/类名/版本/端口/命令/错误码。"""
    entities: list[str] = []

    def add(value: str) -> None:
        value = value.strip().strip("`\"'")
        if len(value) >= 2 and value not in entities:
            entities.append(value)

    for match in VERSION_RE.findall(question):
        add(match)
    for match in PORT_RE.findall(question):
        add(match)
    for match in FLAG_RE.findall(question):
        add(match)
    for token in TOKEN_RE.findall(question):
        if CAMEL_SNAKE_RE.match(token) or CAMEL_CASE_RE.search(token):
            add(token)
        elif token.isupper() and len(token) >= 3:
            add(token)
    return entities


def entity_coverage(question: str, chunks: list[dict], top_n: int = 3) -> float:
    """问题实体在 TopN 片段中的命中比例；无精确实体时返回 1.0（不惩罚概念题）。"""
    entities = extract_entities(question)
    if not entities:
        return 1.0
    text = "\n".join(str(c.get("text") or "") for c in chunks[:top_n]).lower()
    if not text:
        return 0.0
    hit = sum(1 for entity in entities if entity.lower() in text)
    return hit / len(entities)


def _relevance(chunks: list[dict], confidence: float | None) -> float:
    """取 TopN 的相关度：rerank 分优先，无 rerank 的片段用自己的分数。

    混合来源（本地 + 网页）时，网页片段通常只有融合分而没有 rerank 分，
    不能因为本地片段带了低 rerank 分就把整体相关性拉低成 MISS。
    """
    top = chunks[:3]
    scores: list[float] = []
    for chunk in top:
        if chunk.get("rerank_score") is not None:
            scores.append(float(chunk["rerank_score"]))
            continue
        # 无 rerank 分时优先用向量相似度；RRF 融合分（约 0.01）不能代表相关性
        score = max(
            float(chunk.get("vector_score", 0.0) or 0.0),
            float(chunk.get("score", 0.0) or 0.0),
        )
        if score > 0:
            scores.append(score)
    if scores:
        return max(scores)
    if confidence is not None and float(confidence) > 0:
        return float(confidence)
    return 0.0


def _parse_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_stale(
    chunks: list[dict],
    question: str,
    stale_days: int = 180,
    now: datetime | None = None,
) -> bool:
    """命中但文档过旧，且问题带时效要求 → STALE。"""
    if not any(term in question for term in RECENCY_TERMS):
        return False
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=stale_days)
    dates: list[datetime] = []
    for chunk in chunks[:3]:
        meta = chunk.get("metadata") or {}
        for key in ("retrieved_at", "updated_at", "created_at"):
            parsed = _parse_time(meta.get(key))
            if parsed is not None:
                dates.append(parsed)
    if not dates:
        return False
    return max(dates) < cutoff


def judge_answerability(
    chunks: list[dict],
    question: str,
    *,
    threshold: float = 0.45,
    entity_coverage_min: float = 0.50,
    stale_days: int = 180,
    confidence: float | None = None,
    now: datetime | None = None,
) -> Answerability:
    """本地证据充足性：HIT / PARTIAL / MISS / STALE（PRD E.5）。"""
    if not chunks:
        return Answerability.MISS
    relevance = _relevance(chunks, confidence)
    coverage = entity_coverage(question, chunks)
    if relevance < 0.30 or coverage == 0.0:
        return Answerability.MISS
    if relevance >= threshold and coverage >= entity_coverage_min:
        if is_stale(chunks, question, stale_days=stale_days, now=now):
            return Answerability.STALE
        return Answerability.HIT
    return Answerability.PARTIAL


def fit_context_chunks(chunks: list[dict], max_chars: int = 8000) -> list[dict]:
    """按上下文预算裁剪片段，保留编号顺序与完整片段优先原则。"""
    if max_chars <= 0:
        return []
    selected: list[dict] = []
    used = 0
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        remaining = max_chars - used - CONTEXT_META_OVERHEAD
        if remaining <= 0:
            break
        if len(text) <= remaining:
            selected.append(chunk)
            used += CONTEXT_META_OVERHEAD + len(text)
            continue
        copy = dict(chunk)
        copy["text"] = text[:remaining]
        selected.append(copy)
        break
    return selected
