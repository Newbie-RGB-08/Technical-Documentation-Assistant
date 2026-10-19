"""ChromaDB 向量存储实现（PRD 3.2.2 / 3.2.5）。

多集合隔离：tech_docs / personal_notes 按来源拆分；
批量入库（100/批）；upsert 语义支持增量重入。
"""
from __future__ import annotations

import threading

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from app.config import get_settings
from app.services.base_stores import VectorStore

_COLLECTIONS = {"tech_doc": "tech_docs", "personal_note": "personal_notes"}
_BATCH_SIZE = 100


class ChromaStore(VectorStore):
    def __init__(self, persist_path: str | None = None, model_name: str | None = None):
        settings = get_settings()
        self.client = chromadb.PersistentClient(path=str(persist_path or settings.chroma_path))
        self.embed_fn = SentenceTransformerEmbeddingFunction(
            model_name=model_name or settings.embedding_model
        )
        self._lock = threading.Lock()

    def _collection(self, source_type: str):
        name = _COLLECTIONS.get(source_type)
        if name is None:
            raise ValueError(f"未知 source_type: {source_type}")
        return self.client.get_or_create_collection(
            name=name,
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def make_id(file_path: str, chunk_index: int, content_hash: str | None = None) -> str:
        """文件内 chunk 的稳定 ID；带 content_hash 时，内容变更会产生新向量 ID。"""
        base = f"{file_path}#{chunk_index}"
        return f"{base}#{content_hash[:12]}" if content_hash else base

    def add_chunks(self, chunks: list[dict], source_type: str) -> list[str]:
        """chunks 的 metadata 必须含 file_path 与 global_index（文件内全局序号，
        由 ingest_pipeline 分配，避免分节 chunk_index 冲突）。"""
        collection = self._collection(source_type)
        ids: list[str] = []
        with self._lock:
            for i in range(0, len(chunks), _BATCH_SIZE):
                batch = chunks[i : i + _BATCH_SIZE]
                batch_ids = [
                    self.make_id(
                        c["metadata"]["file_path"],
                        c["metadata"]["global_index"],
                        c["metadata"].get("content_hash"),
                    )
                    for c in batch
                ]
                collection.upsert(
                    ids=batch_ids,
                    documents=[c["text"] for c in batch],
                    metadatas=[c["metadata"] for c in batch],
                )
                ids.extend(batch_ids)
        return ids

    def query(
        self,
        query_text: str,
        top_k: int,
        source_type: str | None = None,
        where: dict | None = None,
    ) -> list[dict]:
        results: list[dict] = []
        targets = [source_type] if source_type else list(_COLLECTIONS.keys())
        with self._lock:
            for st in targets:
                col = self._collection(st)
                if col.count() == 0:
                    continue
                res = col.query(query_texts=[query_text], n_results=min(top_k, col.count()), where=where)
                docs = res.get("documents") or [[]]
                if not docs or not docs[0]:
                    continue
                ids = res["ids"][0]
                metadatas = (res.get("metadatas") or [[]])[0]
                distances = (res.get("distances") or [[]])[0]
                for i, text in enumerate(docs[0]):
                    score = 1.0 - distances[i] if i < len(distances) else 0.0
                    results.append(
                        {
                            "id": ids[i],
                            "text": text,
                            "metadata": metadatas[i] if i < len(metadatas) else {},
                            "score": score,
                            "source_type": st,
                        }
                    )
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def delete_by_ids(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock:
            for st, name in _COLLECTIONS.items():
                self._collection(st).delete(ids=ids)

    def delete_by_metadata(self, source_type: str, where: dict) -> None:
        with self._lock:
            self._collection(source_type).delete(where=where)

    def count(self, source_type: str) -> int:
        return self._collection(source_type).count()
