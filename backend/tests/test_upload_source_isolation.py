"""P4：上传来源隔离与来源卡字段（AC11）。"""
from __future__ import annotations

from app.models.schemas import SourceItem
from app.services.rag_service import merge_chunks_to_sources


def _chunk(file_path: str, source_type: str, title: str, text: str, **extra):
    return {
        "text": text,
        "metadata": {
            "file_path": file_path,
            "source_type": source_type,
            "title": title,
            "heading_path": title,
            **extra,
        },
        "score": 0.8,
    }


def test_uploaded_doc_same_title_is_not_merged_with_tech_doc():
    cards = merge_chunks_to_sources(
        [
            _chunk("data/tech_docs/guide.md", "tech_doc", "同名文档", "网页内容"),
            _chunk(
                "data/uploads/u_1/original.pdf",
                "uploaded_doc",
                "同名文档",
                "上传内容",
                origin="upload",
                upload_id="u_1",
                doc_format="pdf",
                original_filename="同名文档.pdf",
            ),
        ]
    )
    assert len(cards) == 2
    by_type = {card["source_type"]: card for card in cards}
    uploaded = by_type["uploaded_doc"]
    assert uploaded["upload_id"] == "u_1"
    assert uploaded["doc_format"] == "pdf"
    assert uploaded["original_filename"] == "同名文档.pdf"
    assert uploaded["origin"] == "upload"
    assert uploaded["trust_level"] == "local_high"
    assert uploaded.get("is_hybrid") is not True
    item = SourceItem(**uploaded)
    assert item.upload_id == "u_1" and item.source_type == "uploaded_doc"


def test_uploaded_doc_same_title_as_personal_note_is_not_merged():
    cards = merge_chunks_to_sources(
        [
            _chunk("data/notes/same.md", "personal_note", "同名标题", "本地笔记"),
            _chunk(
                "data/uploads/u_2/original.md",
                "uploaded_doc",
                "同名标题",
                "上传文档",
                origin="upload",
                upload_id="u_2",
                doc_format="md",
                original_filename="同名标题.md",
            ),
        ]
    )
    assert len(cards) == 2
    assert {card["source_type"] for card in cards} == {"personal_note", "uploaded_doc"}
    assert all(card.get("is_hybrid") is not True for card in cards)


def test_old_source_cards_do_not_gain_required_upload_fields():
    card = merge_chunks_to_sources(
        [_chunk("data/notes/old.md", "personal_note", "旧笔记", "旧内容")]
    )[0]
    item = SourceItem(**card)
    assert item.upload_id is None
    assert item.doc_format is None
    assert item.original_filename is None
