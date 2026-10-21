"""增量入库管道（PRD 3.2.4）。

SHA-256 变更检测 → 删旧向量 → 重新分块向量化 → 写元数据；
未变更文档跳过；force_reingest 强制全量重入。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.chunker import chunk_markdown
from app.services.base_stores import MetadataStore, VectorStore
from app.services.note_scanner import scan_markdown_files


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sanitize_metadata(meta: dict) -> dict:
    """Chroma 元数据仅支持 str/int/float/bool：tags 列表转逗号串，剔除非标量。"""
    clean: dict = {}
    for key, value in meta.items():
        if isinstance(value, (str, int, float, bool)) and value is not None:
            clean[key] = value
        elif isinstance(value, list):
            clean[key] = ",".join(str(v) for v in value)
    return clean


def prepare_chunks(
    file_record: dict,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    content_hash: str | None = None,
) -> list[dict]:
    """对单个文件记录分块，并补齐 global_index、content_hash、清洗元数据。"""
    file_meta = {
        "file_path": file_record["file_path"],
        "source_type": file_record["source_type"],
        "title": file_record.get("title") or Path(file_record["file_path"]).stem,
        "tags": file_record.get("tags", []),
        "created_at": file_record.get("created_at", ""),
        "updated_at": file_record.get("updated_at", ""),
        "source_url": file_record.get("source_url") or "",
        "canonical_url": file_record.get("canonical_url") or "",
        "origin": file_record.get("origin") or "",
        "retrieved_at": file_record.get("retrieved_at") or "",
        "expires_at": file_record.get("expires_at") or "",
        "search_query": file_record.get("search_query") or "",
        "provider": file_record.get("provider") or "",
        "content_hash": content_hash or compute_content_hash(file_record["content"]),
    }
    chunks = chunk_markdown(
        file_record["content"], file_meta, chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    for i, chunk in enumerate(chunks):
        chunk["metadata"]["global_index"] = i
        chunk["metadata"] = sanitize_metadata(chunk["metadata"])
    return chunks


def ingest_files(
    files: list[dict],
    vector_store: VectorStore,
    metadata_store: MetadataStore,
    force_reingest: bool = False,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    on_file_ingested=None,
) -> dict:
    """入库一批文件，返回 {ingested, skipped, failed, total_chunks, errors}。

    on_file_ingested：单文件成功入库后的回调（用于后台刷新 BM25 / 关键字权重）。
    """
    summary = {"ingested": 0, "skipped": 0, "failed": 0, "total_chunks": 0, "errors": []}

    for record in files:
        file_path = record["file_path"]
        try:
            content_hash = compute_content_hash(record["content"])
            existing = metadata_store.get_document(file_path)

            if existing and existing["content_hash"] == content_hash and not force_reingest:
                summary["skipped"] += 1
                continue

            # 有变更：删除旧向量与旧元数据
            if existing:
                old_chunks = metadata_store.get_chunks_by_document(existing["id"])
                old_ids = [c["chroma_id"] for c in old_chunks]
                if old_ids:
                    vector_store.delete_by_ids(old_ids)
                vector_store.delete_by_metadata(
                    record["source_type"], {"file_path": file_path}
                )
                metadata_store.delete_document(file_path)

            chunks = prepare_chunks(record, chunk_size, chunk_overlap, content_hash=content_hash)
            vector_ids = vector_store.add_chunks(chunks, record["source_type"])

            doc_id = metadata_store.upsert_document(
                {
                    "file_path": file_path,
                    "source_type": record["source_type"],
                    "title": record.get("title"),
                    "source_url": record.get("source_url") or "",
                    "content_hash": content_hash,
                    "chunk_count": len(chunks),
                }
            )
            metadata_store.save_chunks(doc_id, chunks, vector_ids)
            if record.get("tags"):
                metadata_store.save_tags(doc_id, record["tags"])

            summary["ingested"] += 1
            summary["total_chunks"] += len(chunks)
            if on_file_ingested is not None:
                try:
                    on_file_ingested(
                        {
                            "file_path": file_path,
                            "document_id": doc_id,
                            "chunk_count": len(chunks),
                            "source_type": record["source_type"],
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - 回调失败不影响入库结果
                    summary["errors"].append({"file_path": file_path, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 单文件失败不阻断整批
            summary["failed"] += 1
            summary["errors"].append({"file_path": file_path, "error": str(exc)})

    return summary


def ingest_directory(
    directory: str | Path,
    source_type: str,
    vector_store: VectorStore,
    metadata_store: MetadataStore,
    force_reingest: bool = False,
) -> dict:
    """扫描目录并入库（API 层入口）。"""
    files = scan_markdown_files(directory, source_type=source_type)
    return ingest_files(
        files,
        vector_store,
        metadata_store,
        force_reingest=force_reingest,
    )
