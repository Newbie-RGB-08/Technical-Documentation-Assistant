"""阶段 5 测试：三口径验证（Chroma / MySQL 一致性）——真实库口径。

前置：阶段 4 真实入库已执行。全部断言基于真实 rag_meta + chroma_db。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings


@pytest.fixture(scope="module")
def real_stats():
    from app.services.metadata_store import MySQLMetadataStore
    from app.services.vector_store import ChromaStore
    from scripts.verify_tech_docs import collect_stats

    settings = get_settings()
    vector_store = ChromaStore(persist_path=str(settings.chroma_path))
    metadata_store = MySQLMetadataStore(url=settings.mysql_url)
    return collect_stats(vector_store, metadata_store)


def test_chroma_mysql_chunk_counts_equal(real_stats):
    assert real_stats["documents"] > 0, "tech_doc 文档数为 0，请先执行阶段 4 入库"
    assert real_stats["chroma_chunks"] == real_stats["mysql_chunks"], (
        f"Chroma={real_stats['chroma_chunks']} vs MySQL={real_stats['mysql_chunks']}"
    )


def test_no_per_document_mismatch(real_stats):
    assert real_stats["mismatches"] == [], f"存在文档级不一致: {real_stats['mismatches'][:5]}"


def test_four_language_dirs_present(real_stats):
    by_lang = real_stats["by_lang"]
    for lang in ("java", "python", "cpp", "go"):
        assert lang in by_lang, f"{lang} 目录无入库记录: {by_lang}"
        assert by_lang[lang]["docs"] >= 15, f"{lang} 入库文档过少: {by_lang[lang]}"
        assert by_lang[lang]["chunks"] >= by_lang[lang]["docs"], f"{lang} 分块异常: {by_lang[lang]}"


def test_documents_carry_source_url(real_stats_meta=None):
    """documents 表的 tech_doc 记录应有 source_url（溯源）。"""
    from app.services.metadata_store import MySQLMetadataStore

    settings = get_settings()
    store = MySQLMetadataStore(url=settings.mysql_url)
    docs = store.list_documents(source_type="tech_doc")
    assert docs
    missing = [d["file_path"] for d in docs if not d.get("source_url")]
    assert not missing, f"{len(missing)} 个文档缺 source_url，例如 {missing[:3]}"
