"""共享 fixtures：Chroma 临时库 + MySQL 测试库 + 样例笔记目录。"""
import os
import pathlib

# 必须在 chromadb/sentence_transformers 导入前设置，否则模型检查会直连 huggingface.co 并长时间重试
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.services.metadata_store import MySQLMetadataStore
from app.services.vector_store import ChromaStore

SAMPLE_NOTES = {
    "a_chroma.md": (
        "---\ntitle: Chroma 入门\ntags: [chroma]\n---\n\n"
        "# Chroma 入门\n\nPersistentClient 用于持久化存储。\n"
    ),
    "b_retrieval.md": (
        "# 检索策略\n\n混合检索结合向量与 BM25，RRF 融合两路结果。\n"
    ),
    "c_llm.md": "# LLM 网关\n\n统一网关支持 Ollama 与 OpenAI 兼容协议。\n",
}


def _test_mysql_url() -> str:
    settings = get_settings()
    return settings.mysql_url.replace("/rag_meta?", "/rag_meta_test?")


def _ensure_test_db() -> None:
    url = _test_mysql_url()
    admin = sa.create_engine(url.rsplit("/", 1)[0] + "/")
    with admin.connect() as conn:
        conn.execute(sa.text("CREATE DATABASE IF NOT EXISTS rag_meta_test CHARACTER SET utf8mb4"))
        conn.commit()


@pytest.fixture(scope="session")
def stores(tmp_path_factory):
    chroma_dir = tmp_path_factory.mktemp("chroma")
    _ensure_test_db()
    return ChromaStore(persist_path=str(chroma_dir)), MySQLMetadataStore(url=_test_mysql_url())


@pytest.fixture(scope="session")
def notes_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("notes")
    for name, text in SAMPLE_NOTES.items():
        (d / name).write_text(text, encoding="utf-8")
    return d
