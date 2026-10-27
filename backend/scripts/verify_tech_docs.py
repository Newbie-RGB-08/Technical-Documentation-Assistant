"""三口径验证（语言学习资料爬取入库实施方案 步骤 5）。

口径① ChromaDB tech_docs 集合向量数；
口径② MySQL rag_meta documents/chunks 计数（按语言目录分组）；
口径③ 两边一致性：每份文档 MySQL chunk_count 与 Chroma 内实际向量数一致，
      全库 Chroma 向量总数 = MySQL chunks 行数。

用法: python scripts/verify_tech_docs.py  （针对真实 rag_meta + chroma_db）
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings


def _lang_of(file_path: str) -> str:
    """data/tech_docs/<lang>/<file>.md -> <lang>。"""
    parts = Path(file_path).parts
    if "tech_docs" in parts:
        i = parts.index("tech_docs")
        if i + 1 < len(parts):
            return parts[i + 1]
    return "other"


def collect_stats(vector_store, metadata_store, source_type: str = "tech_doc") -> dict:
    """三口径统计（vector_store/metadata_store 参数化，测试可注入临时库）。"""
    chroma_total = vector_store.count(source_type)

    docs = metadata_store.list_documents(source_type=source_type)
    by_lang: dict[str, dict] = defaultdict(lambda: {"docs": 0, "chunks": 0})
    for doc in docs:
        lang = _lang_of(doc["file_path"])
        by_lang[lang]["docs"] += 1
        by_lang[lang]["chunks"] += int(doc.get("chunk_count") or 0)

    # 口径③：逐文档比对 MySQL chunk_count 与 Chroma 实际向量数
    mismatches: list[dict] = []
    for doc in docs:
        chunks = metadata_store.get_chunks_by_document(doc["id"])
        actual = len({c["chroma_id"] for c in chunks})
        if actual != int(doc.get("chunk_count") or 0):
            mismatches.append(
                {"file_path": doc["file_path"], "mysql": doc["chunk_count"], "chroma": actual}
            )

    mysql_chunks_total = sum(int(d.get("chunk_count") or 0) for d in docs)
    return {
        "chroma_chunks": chroma_total,
        "mysql_chunks": mysql_chunks_total,
        "documents": len(docs),
        "by_lang": dict(by_lang),
        "mismatches": mismatches,
        "consistent": chroma_total == mysql_chunks_total and not mismatches,
    }


def print_report(stats: dict) -> bool:
    print(f"documents={stats['documents']}  chroma_chunks={stats['chroma_chunks']}  "
          f"mysql_chunks={stats['mysql_chunks']}")
    for lang, s in sorted(stats["by_lang"].items()):
        print(f"  [{lang}] docs={s['docs']} chunks={s['chunks']}")
    for m in stats["mismatches"]:
        print(f"  MISMATCH {m['file_path']}: mysql={m['mysql']} chroma={m['chroma']}")
    ok = stats["consistent"] and stats["documents"] > 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


def main() -> int:
    from app.services.metadata_store import MySQLMetadataStore
    from app.services.vector_store import ChromaStore

    settings = get_settings()
    vector_store = ChromaStore(persist_path=str(settings.chroma_path))
    metadata_store = MySQLMetadataStore(url=settings.mysql_url)
    stats = collect_stats(vector_store, metadata_store)
    return 0 if print_report(stats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
