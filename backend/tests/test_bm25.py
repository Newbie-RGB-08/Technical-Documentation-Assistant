"""阶段 2 测试：BM25 关键词索引（基于已入库语料）。"""
from app.core.bm25_index import BM25Index


def test_bm25_build_and_search(stores):
    vector_store, _ = stores
    # 自包含语料：不依赖其他测试的入库顺序
    vector_store.add_chunks(
        [
            {
                "text": "混合检索结合向量与 BM25，RRF 融合两路结果。",
                "metadata": {"file_path": "bm25/retrieval.md", "global_index": 0},
            },
            {
                "text": "PersistentClient 用于持久化存储。",
                "metadata": {"file_path": "bm25/chroma.md", "global_index": 0},
            },
        ],
        source_type="personal_note",
    )
    index = BM25Index()
    loaded = index.load_from_chroma(vector_store)
    assert loaded > 0

    hits = index.search("混合检索 BM25", top_k=3)
    assert hits, "应能检索到 BM25 相关内容"
    assert hits[0]["score"] > 0
    assert "text" in hits[0] and "metadata" in hits[0]

    # 精确关键词优先命中含该词的文档
    hits2 = index.search("PersistentClient", top_k=3)
    assert hits2 and "PersistentClient" in hits2[0]["text"]


def test_bm25_loads_uploaded_doc_collection(stores):
    """P1：BM25 必须遍历 uploaded_docs，而不是只硬编码两种旧类型。"""
    vector_store, _ = stores
    path = "bm25/uploaded-only.md"
    try:
        vector_store.add_chunks(
            [
                {
                    "text": "上传文档专用关键词：盘古文档、扫描件与表格解析。",
                    "metadata": {
                        "file_path": path,
                        "global_index": 0,
                        "content_hash": "f" * 64,
                    },
                }
            ],
            source_type="uploaded_doc",
        )
        index = BM25Index()
        assert index.load_from_chroma(vector_store) > 0
        hits = index.search("盘古文档 扫描件", top_k=5)
        assert hits and any("盘古文档" in h["text"] for h in hits)
    finally:
        vector_store.delete_by_metadata("uploaded_doc", {"file_path": path})


def test_bm25_incremental_add():
    index = BM25Index()
    index.add_documents(
        [
            {"text": "ChromaDB 是向量数据库", "metadata": {"file_path": "x.md", "global_index": 0}},
            {"text": "FastAPI 提供异步接口", "metadata": {"file_path": "x.md", "global_index": 1}},
        ]
    )
    hits = index.search("向量数据库", top_k=2)
    assert hits and "ChromaDB" in hits[0]["text"]

    # 增量追加后懒重建
    index.add_documents(
        [{"text": "RRF 融合多路检索结果", "metadata": {"file_path": "y.md", "global_index": 0}}]
    )
    hits = index.search("RRF", top_k=1)
    assert hits and "RRF" in hits[0]["text"]


def test_bm25_empty_index():
    assert BM25Index().search("任意查询", top_k=3) == []
