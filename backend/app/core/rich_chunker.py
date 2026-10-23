"""上传文档的表格/图片感知分块器（PRD 7.7 / P3）。

旧 ``chunk_markdown`` 保持原行为；本模块只用于 ``uploaded_doc``。图片生成
figure chunk，表格按行分组并重复表头，所有 chunk 都带 content_type 等元数据。
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.chunker import split_sections

IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]*)\)\s*$")
PAGE_RE = re.compile(r"第\s*(\d+)\s*页")
SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
TEXT_SEPARATORS = ["\n\n", "\n", "。", "；", "，", " ", ""]


def _make_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=TEXT_SEPARATORS,
        length_function=len,
    )


def _extract_page_no(text: str, fallback=None):
    matches = PAGE_RE.findall(text or "")
    if matches:
        return int(matches[-1])
    return fallback


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(SEPARATOR_CELL_RE.match(cell.strip()) for cell in cells)


def _asset_index(file_meta: dict) -> dict[str, dict]:
    raw = file_meta.get("_assets") or file_meta.get("assets") or []
    result: dict[str, dict] = {}
    for asset in raw:
        if isinstance(asset, dict):
            data = dict(asset)
        else:
            data = {
                "name": getattr(asset, "name", ""),
                "rel_path": getattr(asset, "rel_path", ""),
                "caption": getattr(asset, "caption", ""),
                "ocr_text": getattr(asset, "ocr_text", ""),
                "page_no": getattr(asset, "page_no", None),
                "asset_type": getattr(asset, "kind", "image"),
            }
        for key in (data.get("rel_path"), data.get("name")):
            if key:
                result[str(key)] = data
                result[PurePosixPath(str(key)).name] = data
    return result


def _figure_chunk(
    line: str,
    file_meta: dict,
    assets: dict[str, dict],
    page_no,
    image_index: int,
) -> dict | None:
    match = IMAGE_LINE_RE.match(line.strip())
    if not match:
        return None
    alt, rel_path = match.group(1).strip(), match.group(2).strip()
    # 空图（无资源路径）不生成空 figure chunk；alt 文本不单独构成图片。
    if not rel_path:
        return None
    name = PurePosixPath(rel_path).name
    if not alt and not name:
        return None
    info = assets.get(rel_path) or assets.get(name) or {}
    caption = str(info.get("caption") or alt or name).strip()
    ocr_text = str(info.get("ocr_text") or "").strip()
    if not caption and not ocr_text and not name:
        return None
    asset_page = info.get("page_no") or page_no
    lines = [f"图片：{caption or name}"]
    if ocr_text:
        lines.append(f"OCR：{ocr_text}")
    if name:
        lines.append(f"文件名：{name}")
    return {
        "text": "\n".join(lines),
        "metadata": {
            "content_type": "figure",
            "image_index": image_index,
            "asset_name": name,
            "page_no": asset_page,
        },
    }


def _table_chunks(
    lines: list[str],
    file_meta: dict,
    page_no,
    table_index: int,
    chunk_size: int,
) -> list[dict]:
    rows = [_table_cells(line) for line in lines if line.strip()]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return []
    header = rows[0]
    width = max(len(row) for row in rows)
    header = header + [""] * (width - len(header))
    body = [row + [""] * (width - len(row)) for row in rows[1:]]
    body = [row for row in body if not _is_separator_row(row)]
    if not body:
        return []
    separator = ["---"] * width
    header_md = "| " + " | ".join(header) + " |"
    separator_md = "| " + " | ".join(separator) + " |"
    chunks: list[dict] = []
    current: list[str] = []
    current_len = 0
    group_index = 0

    def flush() -> None:
        nonlocal current, current_len, group_index
        if not current:
            return
        prefix = []
        if group_index:
            prefix.append("（续表）")
        text = "\n".join(prefix + [header_md, separator_md] + current)
        chunks.append(
            {
                "text": text,
                "metadata": {
                    "content_type": "table",
                    "table_index": table_index,
                    "page_no": page_no,
                    "row_count": len(body),
                    "col_count": width,
                },
            }
        )
        group_index += 1
        current = []
        current_len = 0

    for row in body:
        row_md = "| " + " | ".join(row) + " |"
        # 至少保留一行；累计超过 chunk_size 才另起一组并重复表头。
        if current and current_len + len(row_md) + 1 > chunk_size:
            flush()
        current.append(row_md)
        current_len += len(row_md) + 1
    flush()
    return chunks


def chunk_uploaded_document(
    text: str,
    file_meta: dict | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[dict]:
    """表格/图片感知分块；返回 chunk 的 metadata 尚未补 global_index/content_hash。"""
    file_meta = file_meta or {}
    base_meta = {
        key: value
        for key, value in file_meta.items()
        if key not in ("assets", "_assets")
    }
    assets = _asset_index(file_meta)
    splitter = _make_splitter(chunk_size, chunk_overlap)
    chunks: list[dict] = []
    table_index = 0
    image_index = 0

    for section in split_sections(text):
        section_chunks: list[dict] = []
        page_no = _extract_page_no(
            f"{section.get('heading_path', '')}\n{section.get('content', '')}",
            base_meta.get("page_no"),
        )
        lines = section["content"].split("\n")
        normal: list[str] = []

        def add_text() -> None:
            nonlocal normal
            content = "\n".join(normal).strip("\n")
            normal = []
            if not content.strip():
                return
            parts = (
                [content]
                if len(content) <= chunk_size
                else splitter.split_text(content)
            )
            for part in parts:
                if part.strip():
                    section_chunks.append(
                        {
                            "text": part,
                            "metadata": {
                                "content_type": "text",
                                "page_no": page_no,
                            },
                        }
                    )

        index = 0
        while index < len(lines):
            line = lines[index]
            if _is_table_line(line):
                add_text()
                table_lines = []
                while index < len(lines) and _is_table_line(lines[index]):
                    table_lines.append(lines[index])
                    index += 1
                for chunk in _table_chunks(
                    table_lines, file_meta, page_no, table_index, chunk_size
                ):
                    section_chunks.append(chunk)
                table_index += 1
                continue
            if IMAGE_LINE_RE.match(line.strip()):
                add_text()
                chunk = _figure_chunk(
                    line, file_meta, assets, page_no, image_index
                )
                if chunk is not None:
                    section_chunks.append(chunk)
                    image_index += 1
                index += 1
                continue
            normal.append(line)
            index += 1
        add_text()

        total = len(section_chunks)
        for chunk_index, chunk in enumerate(section_chunks):
            chunk["metadata"] = {
                **base_meta,
                **chunk["metadata"],
                "heading_path": section["heading_path"],
                "chunk_index": chunk_index,
                "total_chunks": total,
            }
            chunks.append(chunk)
    return chunks
