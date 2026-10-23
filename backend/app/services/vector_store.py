"""ChromaDB 向量存储实现（PRD 3.2.2 / 3.2.5）。

多集合隔离：tech_docs / personal_notes / uploaded_docs 按来源拆分；
批量入库（100/批）；upsert 语义支持增量重入。
"""
from __future__ import annotations

import platform
import threading

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from app.config import get_settings
from app.services.base_stores import VectorStore


def _patch_chromadb_windows_sqlite_path() -> None:
    """兼容 ChromaDB 1.5.9 在 Windows 上的持久化路径缺陷。

    该版本的 Rust 绑定同时收到 ``persist_path`` 与 ``sqlite_db_config.url``
    时，会错误地把完整 sqlite 文件路径交给 SQLite，导致
    ``unable to open database file``。Rust 绑定在 ``url=None`` 时会使用
    ``persist_path`` 自动拼接数据库文件，因此这里只忽略 url，不改其他配置。
    非 Windows 或未安装 Rust 绑定时保持原样。
    """
    if platform.system() != "Windows":
        return
    try:
        import chromadb_rust_bindings as rust_bindings
    except ImportError:
        return
    if getattr(rust_bindings.SqliteDBConfig, "_ptdra_url_compat", False):
        return

    original = rust_bindings.SqliteDBConfig

    def compat_sqlite_config(*args, **kwargs):
        # Chroma 以关键字参数传 url；位置参数形式也兼容。
        if len(args) >= 3:
            args = args[:2] + (None,) + args[3:]
        else:
            kwargs["url"] = None
        return original(*args, **kwargs)

    compat_sqlite_config._ptdra_url_compat = True  # type: ignore[attr-defined]
    rust_bindings.SqliteDBConfig = compat_sqlite_config  # type: ignore[assignment]


_patch_chromadb_windows_sqlite_path()

_COLLECTIONS = {
    "tech_doc": "tech_docs",
    "personal_note": "personal_notes",
    "uploaded_doc": "uploaded_docs",
}
_BATCH_SIZE = 100


class ChromaStore(VectorStore):
    def __init__(self, persist_path: str | None = None, model_name: str | None = None):
        settings = get_settings()
        self._persist_path = str(persist_path or settings.chroma_path)
        self._model_name = model_name or settings.embedding_model
        self._client: chromadb.PersistentClient | None = None
        self._embed_fn: SentenceTransformerEmbeddingFunction | None = None
        self._lock = threading.Lock()
        self._init_error: str | None = None

    def _ensure_client(self) -> chromadb.PersistentClient:
        if self._client is not None:
            return self._client
        if self._init_error:
            raise RuntimeError(f"ChromaDB 初始化失败: {self._init_error}") from None
        try:
            self._client = chromadb.PersistentClient(path=self._persist_path)
            self._embed_fn = SentenceTransformerEmbeddingFunction(model_name=self._model_name)
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"ChromaDB 初始化失败: {self._init_error}") from exc
        return self._client

    def _collection(self, source_type: str):
        name = _COLLECTIONS.get(source_type)
        if name is None:
            raise ValueError(f"未知 source_type: {source_type}")
        client = self._ensure_client()
        assert self._embed_fn is not None
        return client.get_or_create_collection(
            name=name,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def source_types() -> tuple[str, ...]:
        """全部可用来源类型；BM25/健康检查等遍历入口统一使用。"""
        return tuple(_COLLECTIONS)

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
        targets = [source_type] if source_type else list(self.source_types())
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
            for st in self.source_types():
                self._collection(st).delete(ids=ids)

    def delete_by_metadata(self, source_type: str, where: dict) -> None:
        with self._lock:
            self._collection(source_type).delete(where=where)

    def count(self, source_type: str) -> int:
        return self._collection(source_type).count()

    def get_chunks_by_source_url(self, source_url: str) -> list[dict]:
        """按 source_url 取回全部已入库片段（网页写回去重/复用）。"""
        url = str(source_url or "").strip()
        if not url:
            return []
        results: list[dict] = []
        with self._lock:
            for st in self.source_types():
                col = self._collection(st)
                if col.count() == 0:
                    continue
                data = col.get(where={"source_url": url})
                ids = data.get("ids") or []
                documents = data.get("documents") or []
                metadatas = data.get("metadatas") or []
                for i, chunk_id in enumerate(ids):
                    results.append(
                        {
                            "id": chunk_id,
                            "text": documents[i] if i < len(documents) else "",
                            "metadata": metadatas[i] if i < len(metadatas) else {},
                            "source_type": st,
                        }
                    )
        return results
