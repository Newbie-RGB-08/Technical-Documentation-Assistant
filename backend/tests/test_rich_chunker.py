"""P3：rich chunker 的表格重复表头、figure chunk 与元数据。"""
from __future__ import annotations

from app.core.chunker import chunk_markdown
from app.core.rich_chunker import chunk_uploaded_document


def test_text_chunks_keep_heading_path_and_skip_empty():
    text = "# 一级\n\n正文内容。\n"
    chunks = chunk_uploaded_document(text, {"upload_id": "u1"})
    assert chunks
    assert chunks[0]["metadata"]["content_type"] == "text"
    assert chunks[0]["metadata"]["heading_path"] == "一级"
    assert chunks[0]["metadata"]["upload_id"] == "u1"
    assert all(c["text"].strip() for c in chunks)


def test_long_table_repeats_header_and_marks_continuation():
    rows = "\n".join(f"| {i} | 数据{i} |" for i in range(20))
    text = (
        "# 表格页\n\n"
        "| 序号 | 内容 |\n"
        "| --- | --- |\n"
        f"{rows}\n"
    )
    chunks = chunk_uploaded_document(text, {}, chunk_size=40, chunk_overlap=0)
    tables = [c for c in chunks if c["metadata"]["content_type"] == "table"]
    assert len(tables) > 1
    assert all("| 序号 | 内容 |" in c["text"] for c in tables)
    assert all("| --- | --- |" in c["text"] for c in tables)
    assert "（续表）" in tables[1]["text"]
    assert tables[0]["metadata"]["row_count"] == 20
    assert tables[0]["metadata"]["col_count"] == 2
    assert tables[0]["metadata"]["table_index"] == 0


def test_multiple_tables_get_distinct_indexes():
    text = (
        "## 第一张\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
        "## 第二张\n"
        "| C | D |\n| --- | --- |\n| 3 | 4 |\n"
    )
    chunks = chunk_uploaded_document(text, {})
    tables = [c for c in chunks if c["metadata"]["content_type"] == "table"]
    assert len(tables) == 2
    assert [c["metadata"]["table_index"] for c in tables] == [0, 1]


def test_empty_table_is_skipped():
    text = "## 空表\n| A | B |\n| --- | --- |\n"
    chunks = chunk_uploaded_document(text, {})
    assert not [c for c in chunks if c["metadata"]["content_type"] == "table"]


def test_figure_chunk_uses_caption_ocr_filename_and_page():
    text = (
        "## 第 3 页\n\n"
        "![示意图](assets/image_001.png)\n"
        "![空图]()\n"
    )
    file_meta = {
        "_assets": [
            {
                "name": "image_001.png",
                "rel_path": "assets/image_001.png",
                "caption": "系统架构图",
                "ocr_text": "图内文字 OCR",
                "page_no": 3,
            }
        ]
    }
    chunks = chunk_uploaded_document(text, file_meta)
    figures = [c for c in chunks if c["metadata"]["content_type"] == "figure"]
    assert len(figures) == 1
    figure = figures[0]
    assert "系统架构图" in figure["text"]
    assert "图内文字 OCR" in figure["text"]
    assert "image_001.png" in figure["text"]
    assert figure["metadata"]["asset_name"] == "image_001.png"
    assert figure["metadata"]["page_no"] == 3
    assert figure["metadata"]["image_index"] == 0


def test_figure_falls_back_to_filename_and_page_heading():
    text = "## 第 2 页\n\n![](assets/no-caption.png)\n"
    chunks = chunk_uploaded_document(text, {})
    figures = [c for c in chunks if c["metadata"]["content_type"] == "figure"]
    assert figures
    assert "no-caption.png" in figures[0]["text"]
    assert figures[0]["metadata"]["page_no"] == 2


def test_chunk_index_is_section_local_and_total_is_consistent():
    text = "# A\n\n" + ("很长文本。" * 40) + "\n\n| X | Y |\n| --- | --- |\n| 1 | 2 |\n"
    chunks = chunk_uploaded_document(text, {}, chunk_size=80, chunk_overlap=0)
    by_section: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_section.setdefault(chunk["metadata"]["heading_path"], []).append(chunk)
    for section_chunks in by_section.values():
        assert [c["metadata"]["chunk_index"] for c in section_chunks] == list(
            range(len(section_chunks))
        )
        assert all(
            c["metadata"]["total_chunks"] == len(section_chunks) for c in section_chunks
        )


def test_old_chunk_markdown_output_is_unchanged():
    text = "# 标题\n\n正文\n"
    meta = {"file_path": "x.md", "source_type": "personal_note", "title": "T"}
    assert chunk_markdown(text, meta) == [
        {
            "text": "# 标题\n\n正文",
            "metadata": {
                **meta,
                "heading_path": "标题",
                "chunk_index": 0,
                "total_chunks": 1,
            },
        }
    ]
