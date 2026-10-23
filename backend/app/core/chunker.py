"""标题感知 + 递归拆分的 Markdown 分块（PRD 3.2.1）。

策略：
1. 按 `#`~`####` 标题层级拆分为语义块，维护标题栈生成完整 heading_path
2. 代码围栏内的 `#` 不视为标题
3. 超过 chunk_size 的块用 RecursiveCharacterTextSplitter 按中文分隔符递归切分
"""
from __future__ import annotations

import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _is_inside_fence(lines: list[str], idx: int) -> bool:
    """判断第 idx 行是否处于代码围栏内部。"""
    fence_count = 0
    for i in range(idx):
        if FENCE_RE.match(lines[i]):
            fence_count += 1
    return fence_count % 2 == 1


def split_sections(text: str) -> list[dict]:
    """按标题拆分，返回 [{level, title, heading_path, content}]。

    heading_path 形如 "RAG > 检索策略 > 混合检索"（含自身标题）。
    content 包含标题行本身，保证 chunk 自包含。
    """
    lines = text.split("\n")
    sections: list[dict] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    buf: list[str] = []

    def flush(cur_title: str) -> None:
        content = "\n".join(buf).strip("\n")
        if content.strip():
            path = " > ".join(t for _, t in stack) if stack else ""
            sections.append(
                {"title": cur_title, "heading_path": path, "content": content}
            )
        buf.clear()

    for idx, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and not _is_inside_fence(lines, idx):
            level = len(m.group(1))
            title = m.group(2).strip()
            flush(title)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        buf.append(line)

    flush(stack[-1][1] if stack else "")
    return sections


# 兼容旧代码/测试对私有函数的引用；行为与公开函数完全一致。
_split_sections = split_sections


def chunk_markdown(
    text: str,
    file_meta: dict | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[dict]:
    """将 Markdown 文本切分为带元数据的 chunk 列表。

    每个 chunk: {"text": str, "metadata": {**file_meta, heading_path,
    chunk_index, total_chunks}}
    """
    file_meta = file_meta or {}
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        length_function=len,
    )

    chunks: list[dict] = []
    for section in _split_sections(text):
        if len(section["content"]) <= chunk_size:
            sub_chunks = [section["content"]]
        else:
            sub_chunks = splitter.split_text(section["content"])

        for i, sub in enumerate(sub_chunks):
            if not sub.strip():
                continue
            chunks.append(
                {
                    "text": sub,
                    "metadata": {
                        **file_meta,
                        "heading_path": section["heading_path"],
                        "chunk_index": i,
                        "total_chunks": len(sub_chunks),
                    },
                }
            )
    return chunks
