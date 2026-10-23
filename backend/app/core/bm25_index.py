"""BM25 关键词索引（PRD 3.3.3 混合检索的 BM25 支路）。

jieba 分词 + BM25Okapi；语料变更时懒重建（个人数据量级 O(n) 重建可接受）。
ChromaDB 查询失败时的回退检索路径。
"""
from __future__ import annotations

import threading

import jieba
from rank_bm25 import BM25Okapi

from app.services.vector_store import ChromaStore


class BM25Index:
    def __init__(self):
        self._corpus_ids: list[str] = []
        self._corpus_texts: list[str] = []
        self._corpus_metas: list[dict] = []
        self._tokenized: list[list[str]] = []
        self._bm25: BM25Okapi | None = None
        self._dirty = False
        self._lock = threading.Lock()

    def load_from_chroma(self, store: ChromaStore) -> int:
        """从 Chroma 全量加载语料（启动时调用）。"""
        total = 0
        with self._lock:
            self._corpus_ids, self._corpus_texts, self._corpus_metas = [], [], []
            # 统一遍历来源类型：新增 uploaded_doc 等类型时无需再改 BM25。
            for source_type in store.source_types():
                collection = store._collection(source_type)
                data = collection.get(include=["documents", "metadatas"])
                docs = data.get("documents") or []
                ids = data.get("ids") or []
                metas = data.get("metadatas") or []
                for i, doc in enumerate(docs):
                    self._corpus_ids.append(ids[i])
                    self._corpus_texts.append(doc)
                    self._corpus_metas.append(metas[i] if i < len(metas) else {})
                    total += 1
            self._dirty = True
        return total

    def add_documents(self, chunks: list[dict]) -> None:
        """入库后增量追加语料，下次检索时懒重建。"""
        with self._lock:
            for chunk in chunks:
                self._corpus_texts.append(chunk["text"])
                self._corpus_metas.append(chunk["metadata"])
                self._corpus_ids.append(
                    ChromaStore.make_id(
                        chunk["metadata"]["file_path"],
                        chunk["metadata"]["global_index"],
                        chunk["metadata"].get("content_hash"),
                    )
                )
            self._dirty = True

    def _ensure_built(self) -> None:
        if self._dirty or self._bm25 is None:
            self._tokenized = [list(jieba.cut(t)) for t in self._corpus_texts]
            self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None
            self._dirty = False

    @staticmethod
    def _match_fallback(
        query_tokens: list[str], tokenized: list[list[str]], scores
    ) -> list[tuple[int, float]]:
        """小语料下 BM25Okapi 的 IDF 可能整体为 0（如 N=2 且词各出现一次），
        此时退化为“命中查询词数量 + 原始分”排序，保证有效匹配不被误过滤。"""
        q_terms = {t for t in query_tokens if t.strip()}
        ranked: list[tuple[int, float]] = []
        for i, doc_tokens in enumerate(tokenized):
            hit_count = sum(1 for t in q_terms if t in doc_tokens)
            if hit_count:
                ranked.append((i, float(hit_count) + float(scores[i])))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """返回 [{id, text, metadata, score}]，按 BM25 分数降序。"""
        with self._lock:
            if not self._corpus_texts:
                return []
            self._ensure_built()
            query_tokens = list(jieba.cut(query))
            if not any(t.strip() for t in query_tokens):
                return []
            scores = self._bm25.get_scores(query_tokens)
            ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            if not ranked or ranked[0][1] <= 0:
                ranked = self._match_fallback(query_tokens, self._tokenized, scores)
            else:
                ranked = [(i, float(score)) for i, score in ranked if score > 0]
            ranked = ranked[:top_k]
            return [
                {
                    "id": self._corpus_ids[i],
                    "text": self._corpus_texts[i],
                    "metadata": self._corpus_metas[i],
                    "score": float(score),
                }
                for i, score in ranked
            ]
