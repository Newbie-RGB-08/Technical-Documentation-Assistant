---
title: ChromaDB 使用要点
tags: [chromadb, 向量数据库, rag]
created_at: 2026-09-20
---

# ChromaDB 使用要点

## 集合与持久化

ChromaDB 0.5+ 使用 `PersistentClient` 做本地持久化：

```python
import chromadb

client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection(
    name="tech_docs",
    metadata={"hnsw:space": "cosine"},
)
```

## 批量写入

- `add(ids, documents, metadatas, embeddings)` 建议每批 100 条以内。
- ids 必须全局唯一；重复 id 会报 `DuplicateIDError`，需要 `upsert` 或先 delete。

## 查询

```python
result = collection.query(
    query_embeddings=[vec],
    n_results=10,
    where={"source_type": "tech_doc"},
)
```

- `where` 支持 `$eq / $ne / $in / $and / $or`。
- 返回的 `distances` 是余弦距离，相似度 = 1 - distance。

## 运维经验

1. 删除文档时向量与元数据要一起删，否则检索会引用到已失效的 file_path。
2. 单 collection 超过 10 万 chunk 后查询延迟明显上升，要考虑分库或迁 Qdrant。
