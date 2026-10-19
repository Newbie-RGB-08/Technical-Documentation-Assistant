"""混合检索器（PRD 3.3.3）。

分级检索：
- L3：向量 + BM25 → RRF 融合（k=60，权重 1.0/0.7）→ Rerank 精排 → top_k
- L2：向量 + BM25 → RRF 融合，无 Rerank
- L1：纯向量（相似度阈值 0.5）
- L0：兜底（不检索）

带降级的检索：结果为空 / 置信度不足 / 异常时逐级回退 L3→L2→L1→L0。
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.core.confidence import compute_confidence

logger = logging.getLogger(__name__)

VECTOR_SIM_THRESHOLD = 0.5
RERANK_FALLBACK_THRESHOLD = 0.3
VECTOR_SIM_FALLBACK_THRESHOLD = 0.45


class HybridRetriever:
    def __init__(self, vector_store, bm25_index, reranker=None):
        """reranker: 任何实现 rerank(query, documents) -> list[float] 的对象。"""
        settings = get_settings()
        self.vector_store = vector_store
        self.bm25_index = bm25_index
        self.reranker = reranker
        self.rrf_k = settings.rrf_k
        self.vector_weight = settings.vector_weight
        self.bm25_weight = settings.bm25_weight

    # ---- RRF 融合 ----
    def _rrf_fuse(self, vec_results: list[dict], bm25_results: list[dict]) -> list[dict]:
        scores: dict[str, float] = {}
        by_id: dict[str, dict] = {}

        for rank, raw in enumerate(vec_results):
            # 保留原始向量相似度：BM25 支路为空时，RRF 归一化会低估置信度
            item = {**raw, "vector_score": float(raw.get("score", 0.0) or 0.0)}
            scores[item["id"]] = scores.get(item["id"], 0.0) + self.vector_weight / (
                self.rrf_k + rank + 1
            )
            by_id.setdefault(item["id"], item)
        for rank, item in enumerate(bm25_results):
            scores[item["id"]] = scores.get(item["id"], 0.0) + self.bm25_weight / (
                self.rrf_k + rank + 1
            )
            by_id.setdefault(item["id"], item)

        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [{**by_id[cid], "score": score} for cid, score in merged]

    def retrieve(self, query: str, level: int, top_k: int = 5) -> list[dict]:
        if level <= 1:
            return self._vector_only(query, top_k=10)

        vec_n = 30 if level == 3 else 20
        candidate_n = 20 if level == 3 else 8

        vec_results = self.vector_store.query(query, top_k=vec_n)
        bm25_results = self.bm25_index.search(query, top_k=vec_n)

        fused = self._rrf_fuse(vec_results, bm25_results)[:candidate_n]
        if not fused:
            return []

        if level == 2:
            return fused[:top_k]

        # L3：Rerank 精排
        if self.reranker is None:
            logger.warning("Reranker 未配置，L3 降级为 L2 行为")
            return fused[:top_k]
        pairs = [c["text"] for c in fused]
        rerank_scores = self.reranker.rerank(query, pairs)
        for chunk, score in zip(fused, rerank_scores):
            chunk["rerank_score"] = float(score)
        fused.sort(key=lambda c: c["rerank_score"], reverse=True)
        return fused[:top_k]

    def _vector_only(self, query: str, top_k: int = 10) -> list[dict]:
        results = self.vector_store.query(query, top_k=top_k)
        return [r for r in results if r["score"] >= VECTOR_SIM_THRESHOLD]

    # ---- 带降级的检索入口 ----
    def retrieve_with_fallback(
        self, query: str, level: int, top_k: int = 5
    ) -> tuple[list[dict], int, float]:
        """从 level 逐级降级检索，返回 (chunks, 实际使用级别, 置信度)。"""
        current = max(level, 0)
        while current >= 0:
            try:
                chunks = self.retrieve(query, level=current, top_k=top_k)
                confidence = compute_confidence(chunks, current)
                if current == 1 and chunks:
                    # L1 需要同时满足相似度回退阈值
                    if confidence >= VECTOR_SIM_FALLBACK_THRESHOLD:
                        return chunks, current, confidence
                elif chunks and confidence >= RERANK_FALLBACK_THRESHOLD:
                    return chunks, current, confidence
            except Exception as exc:  # noqa: BLE001 - 异常触发降级（PRD 3.3.3）
                logger.warning("检索级别 %s 异常，降级: %s", current, exc)
            current -= 1
        return [], -1, 0.0
