"""存储抽象层接口（PRD 3.2.5）。

VectorStore / MetadataStore 抽象隔离底层实现，
当前实现为 ChromaDB 与 MySQL；未来迁移 Qdrant 时仅需新增实现类。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class VectorStore(ABC):
    """向量存储抽象接口。"""

    @abstractmethod
    def add_chunks(self, chunks: list[dict], source_type: str) -> list[str]:
        """添加 chunk（upsert 语义），返回向量 ID 列表。"""

    @abstractmethod
    def query(
        self,
        query_text: str,
        top_k: int,
        source_type: str | None = None,
        where: dict | None = None,
    ) -> list[dict]:
        """向量检索，返回 [{id, text, metadata, score}]，score 为相似度(1-距离)。"""

    @abstractmethod
    def delete_by_ids(self, ids: list[str]) -> None:
        """按 ID 删除。"""

    @abstractmethod
    def delete_by_metadata(self, source_type: str, where: dict) -> None:
        """按元数据过滤删除（如 file_path）。"""

    @abstractmethod
    def count(self, source_type: str) -> int:
        """集合内 chunk 数量。"""


class MetadataStore(ABC):
    """元数据存储抽象接口。"""

    @abstractmethod
    def upsert_document(self, doc: dict) -> int:
        """插入或更新文档记录，返回 document_id。"""

    @abstractmethod
    def get_document(self, file_path: str) -> dict | None:
        """按路径查询文档。"""

    @abstractmethod
    def save_chunks(self, document_id: int, chunks: list[dict], vector_ids: list[str]) -> None:
        """写入 chunk 映射记录。"""

    @abstractmethod
    def get_chunks_by_document(self, document_id: int) -> list[dict]:
        """查询文档的全部 chunk 记录。"""

    @abstractmethod
    def list_documents(self, source_type: str | None = None) -> list[dict]:
        """列出文档，支持按来源过滤。"""

    @abstractmethod
    def delete_document(self, file_path: str) -> int:
        """删除文档及关联 chunk，返回被删文档的 id（不存在返回 -1）。"""

    @abstractmethod
    def create_task(self, task_id: str, file_count: int) -> None:
        """创建入库任务。"""

    @abstractmethod
    def update_task(self, task_id: str, **fields) -> None:
        """更新入库任务状态。"""

    @abstractmethod
    def get_task(self, task_id: str) -> dict | None:
        """查询入库任务。"""
