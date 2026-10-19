"""V1.2 新增：同一文档多片段合并为一张来源卡片的规则。

核心不变量：indices 并集必须严格等于 1..N —— 正文 [来源N] 依赖它，错一个就引用错位。
"""
from app.services.rag_service import (
    MAX_EXCERPTS,
    build_sources,
    merge_chunks_to_sources,
)


def _chunk(
    file_path: str,
    heading: str = "",
    text: str = "片段正文内容。",
    source_type: str = "tech_doc",
    title: str = "示例文档",
    source_url: str = "",
    score: float = 0.5,
) -> dict:
    return {
        "id": f"{file_path}#{heading}",
        "text": text,
        "metadata": {
            "file_path": file_path,
            "heading_path": heading,
            "source_type": source_type,
            "title": title,
            "source_url": source_url,
        },
        "score": score,
    }


def _assert_indices_cover(chunks: list[dict], cards: list[dict]) -> None:
    collected = [i for card in cards for i in card["indices"]]
    assert sorted(collected) == list(range(1, len(chunks) + 1)), (
        f"indices 必须无缝覆盖 1..{len(chunks)}，实际 {sorted(collected)}"
    )
    assert len(collected) == len(set(collected)), "indices 不允许重复"


def test_same_file_chunks_merge_into_one():
    chunks = [
        _chunk("D:/docs/intro.html", "3.1 基础"),
        _chunk("D:/docs/intro.html", "3.2 流程"),
        _chunk("D:/docs/intro.html", "3.4 进阶"),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 1
    card = cards[0]
    assert card["chunk_count"] == 3
    assert card["indices"] == [1, 2, 3]
    _assert_indices_cover(chunks, cards)


def test_indices_cover_all_chunks_across_mixed_files():
    chunks = [
        _chunk("D:/docs/intro.html", "3.1"),
        _chunk("D:/notes/note.md", "随手记"),
        _chunk("D:/docs/intro.html", "3.2"),
        _chunk("D:/notes/note.md", "待办"),
        _chunk("D:/docs/intro.html", "3.4"),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 2
    _assert_indices_cover(chunks, cards)
    by_path = {card["file_path"]: card for card in cards}
    assert by_path["D:/docs/intro.html"]["indices"] == [1, 3, 5]
    assert by_path["D:/notes/note.md"]["indices"] == [2, 4]


def test_distinct_files_not_merged():
    chunks = [_chunk("D:/docs/a.md"), _chunk("D:/docs/b.md"), _chunk("D:/docs/c.md")]
    cards = build_sources(chunks)
    assert len(cards) == 3
    assert {card["file_path"] for card in cards} == {"D:/docs/a.md", "D:/docs/b.md", "D:/docs/c.md"}


def test_source_type_isolation():
    """同一路径但来源类型不同 → 路径分组仍隔离；标题不同时不触发 hybrid 合并。"""
    chunks = [
        _chunk("D:/shared/x.md", source_type="tech_doc", title="网页版文档"),
        _chunk("D:/shared/x.md", source_type="personal_note", title="本地笔记版"),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 2
    assert {card["source_type"] for card in cards} == {"tech_doc", "personal_note"}
    _assert_indices_cover(chunks, cards)


def test_path_normalization_merges():
    """Windows 反斜杠 / 正斜杠 / 大小写差异必须合并。"""
    chunks = [
        _chunk(r"D:\Docs\Intro.md", "一"),
        _chunk("d:/docs/intro.md", "二"),
        _chunk("  D:/docs/INTRO.MD  ", "三"),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 1
    assert cards[0]["indices"] == [1, 2, 3]


def test_empty_file_path_not_merged():
    """缺 file_path 的片段各成一组，绝不互相合并。"""
    a = _chunk("", "未知一")
    b = _chunk("", "未知二")
    a["metadata"].pop("file_path")
    b["metadata"].pop("file_path")
    chunks = [a, _chunk("D:/docs/a.md", "真实"), b]
    cards = build_sources(chunks)
    assert len(cards) == 3
    _assert_indices_cover(chunks, cards)
    assert [card["file_path"] for card in cards if card["chunk_count"] == 1] == ["未知", "D:/docs/a.md", "未知"]


def test_score_is_max_and_order_desc():
    chunks = [
        _chunk("D:/docs/low.md", "低", score=0.20),
        _chunk("D:/docs/high.md", "高1", score=0.30),
        _chunk("D:/docs/high.md", "高2", score=0.91),
    ]
    cards = build_sources(chunks)
    assert [card["file_path"] for card in cards] == ["D:/docs/high.md", "D:/docs/low.md"]
    assert cards[0]["score"] == 0.91
    assert cards[1]["score"] == 0.20


def test_headings_dedup_preserve_order():
    chunks = [
        _chunk("D:/docs/a.md", "3.1"),
        _chunk("D:/docs/a.md", "3.1"),  # 重复章节只保留一次
        _chunk("D:/docs/a.md", "3.2"),
        _chunk("D:/docs/a.md", ""),  # 空章节不入列表
    ]
    cards = build_sources(chunks)
    assert cards[0]["headings"] == ["3.1", "3.2"]
    # 兼容字段 heading_path 取代表片段（首个片段）的章节
    assert cards[0]["heading_path"] == "3.1"


def test_excerpts_capped_at_max():
    chunks = [_chunk("D:/docs/a.md", f"章节{i}", text=f"第{i}段正文。") for i in range(1, 6)]
    cards = build_sources(chunks)
    card = cards[0]
    assert card["chunk_count"] == 5
    assert len(card["excerpts"]) == MAX_EXCERPTS
    assert card["excerpts"][0].startswith("第1段")
    # 兼容字段 excerpt == 首条摘要
    assert card["excerpt"] == card["excerpts"][0]
    _assert_indices_cover(chunks, cards)


def test_single_chunk_keeps_backward_compatible_shape():
    """单片段组仍返回完整字段，旧前端/旧会话历史可继续按 index 渲染。"""
    chunks = [_chunk("D:/docs/only.md", "唯一章节", source_url="https://example.com/only")]
    cards = build_sources(chunks)
    assert len(cards) == 1
    card = cards[0]
    assert card["index"] == 1
    assert card["indices"] == [1]
    assert card["chunk_count"] == 1
    assert card["headings"] == ["唯一章节"]
    assert card["headings"][0] == card["heading_path"]
    assert card["source_url"] == "https://example.com/only"
    assert card["excerpt"].startswith("片段正文内容")


def test_merge_is_pure_and_does_not_mutate_input():
    chunks = [_chunk("D:/docs/a.md", "一"), _chunk("D:/docs/a.md", "二")]
    snapshot = [dict(chunk) for chunk in chunks]
    merge_chunks_to_sources(chunks)
    assert [chunk["metadata"]["heading_path"] for chunk in chunks] == [
        s["metadata"]["heading_path"] for s in snapshot
    ]


# ===================== V1.3 新增测试 =====================


def test_absolute_and_relative_path_merge_to_same_doc():
    """同一文件以绝对路径与相对路径入库时，必须合并为一张卡。"""
    chunks = [
        _chunk(
            r"D:\programfiles\JetBrains Workspace\Pycharm Workspace\PTDRA\backend\data\notes\chromadb_usage.md",
            "集合与持久化",
            source_type="personal_note",
        ),
        _chunk(
            r"data\notes\chromadb_usage.md",
            "基础",
            source_type="personal_note",
        ),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 1
    assert cards[0]["indices"] == [1, 2]
    _assert_indices_cover(chunks, cards)


def test_hybrid_local_and_web_same_title_merges():
    """personal_note + tech_doc 标题相同 → 合并为 hybrid 卡，优先本地，带双跳转。"""
    chunks = [
        _chunk(
            "D:/docs/tech_chromadb.md",
            "集合与持久化",
            source_type="tech_doc",
            title="ChromaDB 使用要点",
            source_url="https://docs.trychroma.com/getting-started",
        ),
        _chunk(
            "data/notes/chromadb_usage.md",
            "本地实践",
            source_type="personal_note",
            title="ChromaDB 使用要点",
        ),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 1
    card = cards[0]
    assert card["is_hybrid"] is True
    assert card["source_type"] == "personal_note"  # 本地优先
    assert card["file_path"] == "data/notes/chromadb_usage.md"  # 本地路径
    assert card["source_url"] == "https://docs.trychroma.com/getting-started"  # 网页 URL
    assert card["indices"] == [1, 2]
    _assert_indices_cover(chunks, cards)


def test_hybrid_title_normalization_ignores_whitespace_and_case():
    """标题归一化忽略空白与大小写（含全角空格），仍触发 hybrid 合并。"""
    chunks = [
        _chunk(
            "tech.md",
            source_type="tech_doc",
            title="ChromaDB  使用要点",
            source_url="https://example.com/chromadb",
        ),
        _chunk(
            "note.md",
            source_type="personal_note",
            title="chromadb 使用要点",
        ),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 1
    assert cards[0]["is_hybrid"] is True


def test_two_tech_docs_same_title_not_merged():
    """两个 tech_doc 标题相同但路径不同 → 不合并（仅跨类型才按标题合并）。"""
    chunks = [
        _chunk("tech/a.md", source_type="tech_doc", title="Python 速览", source_url="https://a"),
        _chunk("tech/b.md", source_type="tech_doc", title="Python 速览", source_url="https://b"),
    ]
    cards = build_sources(chunks)
    assert len(cards) == 2
    assert all(c.get("is_hybrid") is not True for c in cards)
    _assert_indices_cover(chunks, cards)


def test_excerpt_strips_markdown_heading_lines():
    """摘要必须剥离 markdown 标题行，避免摘要首行重复章节名。"""
    chunks = [
        _chunk(
            "data/notes/x.md",
            text="## 集合与持久化\n\n使用 PersistentClient 做本地持久化。",
            source_type="personal_note",
        )
    ]
    cards = build_sources(chunks)
    excerpt = cards[0]["excerpt"]
    assert not excerpt.lstrip().startswith("#")
    assert "集合与持久化" not in excerpt
    assert "PersistentClient" in excerpt


def test_non_hybrid_cards_have_no_is_hybrid_flag():
    """非 hybrid 卡不带 is_hybrid 字段（或为 False），前端用 is_hybrid is True 判断。"""
    chunks = [_chunk("data/notes/only.md", source_type="personal_note")]
    cards = build_sources(chunks)
    assert cards[0].get("is_hybrid") is not True
