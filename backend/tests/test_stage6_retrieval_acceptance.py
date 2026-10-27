"""阶段 6 测试：四类语言问题检索验收（方案步骤 6）——真实向量库口径。

前置：阶段 4 真实入库已执行。每类问题必须在 top-k 命中预期域名来源并带 source_url 引用。
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings

EXPECTED_DOMAINS = {
    "java": ["roadmap.sh", "javaguide.cn", "docs.oracle.com", "runoob.com"],
    "python": ["docs.python.org", "liaoxuefeng.com", "runoob.com"],
    "cpp": ["learncpp.com", "runoob.com"],
    "go": ["go.dev", "golang.google.cn", "runoob.com"],
}

QUESTIONS = {
    "java": "Java 学习路线应该按什么顺序学？",
    "python": "Python 的装饰器怎么用？",
    "cpp": "C++ 的 move 语义是什么？",
    "go": "Go 的 goroutine 和 channel 怎么入门？",
}


@pytest.fixture(scope="module")
def vector_store():
    from app.services.vector_store import ChromaStore

    settings = get_settings()
    return ChromaStore(persist_path=str(settings.chroma_path))


@pytest.mark.parametrize("lang", ["java", "python", "cpp", "go"])
def test_language_question_retrieval(vector_store, lang):
    hits = vector_store.query(QUESTIONS[lang], top_k=8, source_type="tech_doc")
    assert len(hits) >= 3, f"{lang} 召回不足: {len(hits)} 条"

    domains = []
    for hit in hits:
        url = hit["metadata"].get("source_url", "")
        assert url, f"{lang} 命中片段缺 source_url 引用: {hit['id']}"
        domains.append(urlparse(url).netloc.lower())

    expected = EXPECTED_DOMAINS[lang]
    matched = [
        d for d in domains
        if any(d == e or d.endswith("." + e) for e in expected)
    ]
    assert matched, f"{lang} 未命中预期来源 {expected}，实际: {domains}"

    top_score = hits[0]["score"]
    assert top_score > 0.2, f"{lang} 相关度过低: {top_score}"
