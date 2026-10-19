"""本地 Rerank Provider：bge-reranker-v2-m3 Cross-Encoder（懒加载单例）。"""
from __future__ import annotations

import threading

from app.config import get_settings
from app.providers.base import RerankProvider


class LocalBGEReranker(RerankProvider):
    _instance = None
    _lock = threading.Lock()

    def __init__(self, model_name: str | None = None):
        settings = get_settings()
        self.model_name = model_name or settings.reranker_model
        self._model = None
        self._model_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "LocalBGEReranker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder

                    self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        model = self._ensure_model()
        pairs = [(query, doc) for doc in documents]
        scores = model.predict(pairs)
        return [float(s) for s in scores]
