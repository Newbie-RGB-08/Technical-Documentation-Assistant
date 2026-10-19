"""阶段 1 测试：标题感知分块器（PRD 3.2.1）。"""
from app.core.chunker import chunk_markdown

SAMPLE = """# RAG 系统设计

这是引言段落，介绍整体架构。

## 检索策略

检索策略总览内容。

### 混合检索

混合检索结合向量与 BM25。

```python
# 这不是标题，是代码注释
print("hello")
```

### RRF 融合

RRF 使用 k=60。

## 分块策略

分块说明。
"""


def test_basic_structure_and_heading_path():
    chunks = chunk_markdown(SAMPLE, {"file_path": "x.md"})
    assert chunks, "应产出至少一个 chunk"
    for c in chunks:
        assert set(["text", "metadata"]) <= set(c.keys())
        assert "file_path" in c["metadata"]
        assert "heading_path" in c["metadata"]
        assert "chunk_index" in c["metadata"]
        assert "total_chunks" in c["metadata"]

    paths = {c["metadata"]["heading_path"] for c in chunks}
    assert "RAG 系统设计 > 检索策略 > 混合检索" in paths
    assert "RAG 系统设计 > 检索策略 > RRF 融合" in paths
    assert "RAG 系统设计 > 分块策略" in paths


def test_code_fence_hash_not_heading():
    chunks = chunk_markdown(SAMPLE, {})
    code_chunks = [c for c in chunks if 'print("hello")' in c["text"]]
    assert code_chunks, "代码块应被保留"
    # 代码注释所在 chunk 的标题路径必须是 混合检索 小节，而不是新造的标题
    assert all(
        c["metadata"]["heading_path"] == "RAG 系统设计 > 检索策略 > 混合检索"
        for c in code_chunks
    )


def test_no_content_loss():
    chunks = chunk_markdown(SAMPLE, {})
    joined = "\n".join(c["text"] for c in chunks)
    for line in SAMPLE.splitlines():
        if line.strip():
            assert line.strip() in joined, f"内容丢失: {line.strip()}"


def test_long_section_split_with_overlap():
    paragraph = "这是一个很长的句子用来撑长度。" * 80  # ~720 字
    text = "# 长章节\n\n" + paragraph
    chunks = chunk_markdown(text, {}, chunk_size=512, chunk_overlap=64)
    assert len(chunks) > 1, "超长章节应被递归拆分"
    assert all(c["metadata"]["total_chunks"] == len(chunks) for c in chunks)
    assert [c["metadata"]["chunk_index"] for c in chunks] == list(range(len(chunks)))
    # overlap 生效：相邻 chunk 存在公共文本
    assert chunks[0]["text"][30:] in chunks[1]["text"] or chunks[0]["text"][-30:] in chunks[1]["text"]
    assert all(len(c["text"]) <= 512 + 64 for c in chunks), "chunk 不应显著超过 chunk_size+overlap"


def test_intro_before_any_heading_gets_empty_path():
    chunks = chunk_markdown(SAMPLE, {})
    intro = [c for c in chunks if "引言段落" in c["text"]]
    assert intro and intro[0]["metadata"]["heading_path"] == "RAG 系统设计"


def test_empty_text_returns_empty_list():
    assert chunk_markdown("", {}) == []
    assert chunk_markdown("\n\n   \n", {}) == []
