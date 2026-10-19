"""阶段 3 测试：混合检索器（RRF 手算比对、Rerank 排序、降级路径）。"""
from app.core.retriever import HybridRetriever


class MockVectorStore:
    def __init__(self, results=None, raise_error=False):
        self.results = results or []
        self.raise_error = raise_error

    def query(self, query, top_k, **kwargs):
        if self.raise_error:
            raise RuntimeError("vector store down")
        return self.results[:top_k]


class MockBM25:
    def __init__(self, results=None):
        self.results = results or []

    def search(self, query, top_k):
        return self.results[:top_k]


class MockReranker:
    """按文档中关键词出现次数倒序打分，便于断言。"""

    def rerank(self, query, documents):
        return [float(doc.count(query)) for doc in documents]


def _chunk(id_: str, text: str, score: float) -> dict:
    return {"id": id_, "text": text, "metadata": {}, "score": score}


def test_rrf_fusion_order():
    vec = [_chunk("A", "va", 0.9), _chunk("B", "vb", 0.8), _chunk("C", "vc", 0.7)]
    bm25 = [_chunk("B", "vb", 5.0), _chunk("D", "vd", 4.0), _chunk("E", "ve", 3.0)]
    retriever = HybridRetriever(MockVectorStore(), MockBM25())

    fused = retriever._rrf_fuse(vec, bm25)
    # 手算（k=60, w_v=1.0, w_b=0.7；rank 从 1 计）：
    # B = 1/62 + 0.7/61 ≈ 0.02760 > A = 1/61 ≈ 0.01639 > C = 1/63
    # > D = 0.7/62 > E = 0.7/63
    assert [c["id"] for c in fused] == ["B", "A", "C", "D", "E"]
    assert abs(fused[0]["score"] - (1 / 62 + 0.7 / 61)) < 1e-9


def test_level2_returns_fused_without_rerank():
    vec = [_chunk(f"V{i}", f"t{i}", 0.9 - i * 0.1) for i in range(10)]
    bm25 = [_chunk(f"M{i}", f"m{i}", 9.0 - i) for i in range(10)]
    retriever = HybridRetriever(MockVectorStore(vec), MockBM25(bm25))
    result = retriever.retrieve("查询", level=2, top_k=8)
    assert len(result) == 8
    assert all("rerank_score" not in c for c in result)
    # 分数单调不增
    scores = [c["score"] for c in result]
    assert scores == sorted(scores, reverse=True)


def test_level3_applies_rerank():
    vec = [_chunk("A", "查询 查询 查询", 0.9), _chunk("B", "查询", 0.8), _chunk("C", "无关", 0.7)]
    retriever = HybridRetriever(MockVectorStore(vec), MockBM25(), reranker=MockReranker())
    result = retriever.retrieve("查询", level=3, top_k=3)
    assert [c["id"] for c in result] == ["A", "B", "C"]
    assert result[0]["rerank_score"] == 3.0


def test_level1_vector_only_with_threshold():
    vec = [_chunk("H", "高相似", 0.72), _chunk("L", "低相似", 0.4)]
    retriever = HybridRetriever(MockVectorStore(vec), MockBM25())
    result = retriever.retrieve("查询", level=1)
    assert [c["id"] for c in result] == ["H"]


def test_fallback_downgrades_when_empty():
    retriever = HybridRetriever(MockVectorStore([]), MockBM25([]))
    chunks, level, confidence = retriever.retrieve_with_fallback("查询", level=3, top_k=5)
    assert chunks == [] and level == -1 and confidence == 0.0


def test_fallback_downgrades_when_confidence_low():
    # L3 无 reranker → rerank 分缺省 0 → 降级到 L2；L2 RRF 分足够 → 停在 L2
    vec = [_chunk("A", "相关内容查询", 0.9), _chunk("B", "其他", 0.8)]
    retriever = HybridRetriever(MockVectorStore(vec), MockBM25([_chunk("B", "其他", 3.0)]))
    chunks, level, confidence = retriever.retrieve_with_fallback("查询", level=3, top_k=5)
    assert level == 2
    assert confidence >= 0.3


def test_fallback_downgrades_on_exception():
    retriever = HybridRetriever(MockVectorStore(raise_error=True), MockBM25())
    chunks, level, _ = retriever.retrieve_with_fallback("查询", level=3, top_k=5)
    assert chunks == [] and level == -1
