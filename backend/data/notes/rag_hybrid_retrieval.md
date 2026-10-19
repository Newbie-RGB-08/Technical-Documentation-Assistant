---
title: RAG 混合检索与 RRF 融合
tags: [rag, 检索, bm25, rrf]
created_at: 2026-10-08
---

# RAG 混合检索与 RRF 融合

## 为什么要混合检索

纯向量检索对"精确 API 名、版本号、错误码"不敏感；纯 BM25 对同义表达不敏感。两者融合可以互补。

## RRF 融合公式

Reciprocal Rank Fusion：

```
score(d) = Σ  weight_i / (k + rank_i(d))
```

- k 常用 60，起平滑作用。
- 向量分支权重 1.0，BM25 分支权重 0.7（向量更可靠时）。

## 实现要点

```python
def rrf_fuse(vec_results, bm25_results, k=60, w_vec=1.0, w_bm25=0.7):
    scores = {}
    for rank, doc in enumerate(vec_results):
        scores[doc.id] = scores.get(doc.id, 0) + w_vec / (k + rank + 1)
    for rank, doc in enumerate(bm25_results):
        scores[doc.id] = scores.get(doc.id, 0) + w_bm25 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

## 注意事项

1. RRF 只融合排名，不使用原始分数，因此对分数分布差异鲁棒。
2. 但 RRF 分数不能当置信度用——需要保留原始向量相似度另行计算置信度。
3. 小语料下 BM25 的 IDF 可能全为 0，需要匹配数兜底排序。
