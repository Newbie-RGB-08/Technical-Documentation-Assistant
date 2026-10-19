"""阶段 0 冒烟测试：MySQL / Ollama / ChromaDB / bge 向量化 四项连通性。

用法: python scripts/smoke_env.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import sqlalchemy as sa

from app.config import get_settings


def check_mysql() -> str:
    settings = get_settings()
    engine = sa.create_engine(settings.mysql_url, pool_pre_ping=True)
    with engine.connect() as conn:
        version = conn.execute(sa.text("SELECT VERSION()")).scalar()
    return f"MySQL OK: {version}"


def check_ollama() -> str:
    settings = get_settings()
    resp = httpx.get(f"{settings.ollama_host}/api/tags", timeout=10)
    resp.raise_for_status()
    models = [m["name"] for m in resp.json().get("models", [])]
    return f"Ollama OK: {len(models)} 个模型 ({', '.join(models[:5])}...)"


def check_chroma() -> str:
    import chromadb

    settings = get_settings()
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    col = client.get_or_create_collection("smoke_test")
    return f"ChromaDB OK: collection={col.name}, count={col.count()}"


def check_bge() -> str:
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    model = SentenceTransformer(settings.embedding_model)
    vec = model.encode(["技术文档检索测试"])
    dim = vec.shape[1]
    assert dim > 0
    return f"bge OK: 模型={settings.embedding_model}, 向量维度={dim}"


CHECKS = [check_mysql, check_ollama, check_chroma, check_bge]


def main() -> int:
    failures = 0
    for check in CHECKS:
        name = check.__name__
        try:
            print(f"[PASS] {check()}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {name}: {exc}")
    print(f"\n{4 - failures}/4 项通过")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
