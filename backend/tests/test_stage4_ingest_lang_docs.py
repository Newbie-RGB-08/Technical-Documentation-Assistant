"""阶段 4 测试：tech_doc 目录级增量入库（scan → ingest → Chroma/MySQL 双落库）。

使用 conftest 的 stores fixture（临时 Chroma + rag_meta_test 测试库），
验证方案步骤 4 的核心行为：双库落库一致、front-matter source_url 透传、增量跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingest_pipeline import ingest_directory

SAMPLE_DOC = """---
title: {title}
source_url: https://javaguide.cn/{slug}
created_at: 2026-10-27T10:00:00+08:00
updated_at: 2026-10-27T10:00:00+08:00
source_type: tech_doc
---

# {title}

{body}
"""


def _make_docs(root: Path) -> Path:
    base = root / "tech_docs" / "java"
    base.mkdir(parents=True, exist_ok=True)
    (base / "intro.md").write_text(
        SAMPLE_DOC.format(title="Java 入门", slug="intro", body="Java 是面向对象语言。" * 20),
        encoding="utf-8",
    )
    (base / "collection.md").write_text(
        SAMPLE_DOC.format(title="集合框架", slug="collection", body="ArrayList 与 HashMap 的区别。" * 20),
        encoding="utf-8",
    )
    return root / "tech_docs" / "java"


def test_ingest_directory_dual_store_and_incremental(stores, tmp_path):
    vector_store, metadata_store = stores
    doc_dir = _make_docs(tmp_path)

    summary = ingest_directory(doc_dir, source_type="tech_doc",
                               vector_store=vector_store, metadata_store=metadata_store)
    assert summary["ingested"] == 2
    assert summary["failed"] == 0
    assert summary["total_chunks"] > 0

    # 口径一致：Chroma 向量数 = MySQL chunks 总数
    chroma_count = vector_store.count("tech_doc")
    docs = metadata_store.list_documents(source_type="tech_doc")
    mysql_chunks = sum(d["chunk_count"] for d in docs)
    assert chroma_count == mysql_chunks == summary["total_chunks"]

    # source_url 透传到 chunk 元数据（可溯源）
    chunks = metadata_store.get_chunks_by_document(docs[0]["id"])
    assert all(c.get("chroma_id") for c in chunks)

    # 增量：未变更重跑全部跳过，向量数不变
    summary2 = ingest_directory(doc_dir, source_type="tech_doc",
                                vector_store=vector_store, metadata_store=metadata_store)
    assert summary2["skipped"] == 2 and summary2["ingested"] == 0
    assert vector_store.count("tech_doc") == chroma_count
