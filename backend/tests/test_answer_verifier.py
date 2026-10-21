"""P1：答案逐句校验（防幻觉核心）。"""
from app.core.answer_verifier import (
    detect_conflicts,
    split_sentences,
    verify_answer,
)
from app.core.evidence import build_evidence_pack


def _pack(texts):
    return build_evidence_pack(
        [
            {
                "text": text,
                "metadata": {"file_path": f"data/notes/{i}.md", "title": f"文档{i}"},
                "rerank_score": 0.9,
            }
            for i, text in enumerate(texts, start=1)
        ]
    )


def test_valid_answer_passes():
    pack = _pack(["ChromaDB 使用 PersistentClient 持久化数据。"])
    result = verify_answer(
        "ChromaDB 使用 PersistentClient 持久化数据。[来源1]", pack, question="ChromaDB 怎么持久化？"
    )
    assert result.passed is True
    assert result.illegal_citations == []
    assert result.severe == []
    assert result.unsupported_ratio == 0.0


def test_fact_sentence_without_citation_is_unsupported():
    pack = _pack(["ChromaDB 使用 PersistentClient 持久化数据。"])
    result = verify_answer("ChromaDB 使用 PersistentClient 持久化数据。", pack)
    assert result.passed is False
    assert result.unsupported
    assert "事实句缺少引用" in result.unsupported[0]


def test_illegal_citation_is_removed_and_marked():
    pack = _pack(["ChromaDB 使用 PersistentClient 持久化数据。"])
    result = verify_answer("ChromaDB 使用 PersistentClient 持久化数据。[来源99]", pack)
    assert result.passed is False
    assert 99 in result.illegal_citations
    assert result.severe


def test_fabricated_version_is_severe():
    pack = _pack(["ChromaDB 使用 PersistentClient 持久化数据。"])
    result = verify_answer("ChromaDB 1.5.0 使用 PersistentClient 持久化数据。[来源1]", pack)
    assert result.passed is False
    assert any("1.5.0" in item for item in result.severe)


def test_fabricated_port_is_severe():
    pack = _pack(["ChromaDB 使用 PersistentClient 持久化数据。"])
    result = verify_answer("服务监听 localhost:9999 并持久化数据。[来源1]", pack)
    assert result.passed is False
    assert any("localhost:9999" in item for item in result.severe)


def test_overclaim_is_unsupported():
    pack = _pack(["ChromaDB 使用 PersistentClient 持久化数据。"])
    result = verify_answer("这是官方推荐方案，使用 PersistentClient。[来源1]", pack)
    assert result.passed is False
    assert any("过度断言" in item for item in result.unsupported)


def test_version_conflict_detected():
    pack = _pack(["当前版本 1.0 支持该功能。", "当前版本 2.0 支持该功能。"])
    conflicts = detect_conflicts(pack, "当前版本是什么？")
    assert conflicts and "版本" in conflicts[0]
    result = verify_answer("当前版本是 1.0。[来源1]", pack, question="当前版本是什么？")
    assert result.passed is False
    assert result.conflicts


def test_degenerate_empty_and_unclosed_code():
    pack = _pack(["ChromaDB 使用 PersistentClient 持久化数据。"])
    assert verify_answer("", pack).degenerate is True
    unclosed = verify_answer("示例：\n```python\nprint(1)\n[来源1]", pack)
    assert unclosed.degenerate is True


def test_split_sentences_keeps_code_block_whole():
    answer = "第一句。[来源1]\n\n```python\nprint(1)\nprint(2)\n```\n第二句。[来源2]"
    parts = split_sentences(answer)
    assert any("print(1)" in p and "print(2)" in p and p.count("```") == 2 for p in parts)
    assert "第一句。[来源1]" in parts
