"""RAG 服务编排（PRD 3.5.2 主流程）。

提问评分 →（<60 走 confirm 终止）→ 问题改写 → 检索评分定级 → 降级检索
→ 置信度判定 →（兜底）→ sources → 流式生成 → done。
产出 (event_type, data) 元组流，由路由层格式化为 SSE。
"""
from __future__ import annotations

import logging
import re
from typing import AsyncIterator

from app.core.confidence import (
    CONFIDENCE_HIGH,
    DISCLAIMER,
    INCOMPLETE_NOTICE,
    build_fallback_preamble,
    classify_fallback,
)
from app.core.query_rewriter import rewrite_query
from app.core.retrieval_scorer import RetrievalScorer, decide_level
from app.core.question_scorer import QuestionScorer

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个技术文档研究助手。请严格基于以下检索到的文档片段回答问题。

规则：
1. 如果检索到的内容足以回答问题，请给出准确、自然的回答
2. 每个关键事实、结论或代码示例之后必须立即用 [来源N] 标注出处（N 对应片段序号），不允许省略
3. 如果检索到的内容不足以回答问题，请明确说明"根据现有文档无法确定"
4. 不要编造文档中没有的信息
5. 回答使用Markdown格式，代码块使用```包裹

检索到的文档片段：
{context}
"""

REWRITE_HISTORY_TURNS = 3


def build_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks):
        meta = c.get("metadata", {})
        src = meta.get("source_url") or ""
        src_line = f" | 网页来源：{src}" if src else ""
        parts.append(
            f"[来源{i + 1}] 文件：{meta.get('file_path', '未知')} | "
            f"章节：{meta.get('heading_path', '')}{src_line}\n{c['text']}"
        )
    return "\n\n".join(parts)


MAX_EXCERPTS = 3


def _normalize_doc_path(path) -> str:
    """路径归一化：剥离数据根前缀后再做分隔符/大小写归一（V1.3）。

    同一份文件可能以绝对路径 D:\\...\\backend\\data\\notes\\x.md
    或相对路径 data\\notes\\x.md 入库；若只做大小写归一，两者仍会被
    判为不同文档。这里统一截取 data/ 之后的部分。
    """
    p = str(path or "").strip().replace("\\", "/")
    idx = p.rfind("/data/")
    if idx != -1:
        p = p[idx + len("/data/"):]
    elif p.startswith("data/"):
        p = p[len("data/"):]
    return p.strip("/").casefold()


def _normalize_title(title) -> str:
    """标题归一化：用于跨 source_type 判断是否同一文档（V1.3 hybrid 合并）。"""
    return re.sub(r"[\s\u3000]+", "", str(title or "").strip()).casefold()


def _strip_heading_lines(text: str) -> str:
    """剥离 markdown 标题行，避免摘要首行重复章节名（V1.3）。"""
    return "\n".join(
        line for line in text.split("\n") if not line.lstrip().startswith("#")
    ).strip()


def _group_key(index: int, meta: dict) -> str:
    """同一文档的分组键：source_type + 归一化路径。

    缺 file_path 的片段用它自己的序号单独成组，避免脏元数据把无关片段误合并。
    """
    raw_path = str(meta.get("file_path") or "").strip()
    if not raw_path:
        return f"__nopath__#{index}"
    return f"{str(meta.get('source_type') or '').strip().casefold()}|{_normalize_doc_path(raw_path)}"


def _build_card(members: list[tuple[int, dict]], max_excerpts: int) -> dict:
    """把一组（同文档）片段组装成一张来源卡片。"""
    first_idx, first_chunk = members[0]
    first_meta = first_chunk.get("metadata") or {}

    # title：取组内首次出现的非空值
    title = ""
    for _, chunk in members:
        candidate = str((chunk.get("metadata") or {}).get("title") or "").strip()
        if candidate:
            title = candidate
            break

    headings: list[str] = []
    excerpts: list[str] = []
    for _, chunk in members:
        heading = str((chunk.get("metadata") or {}).get("heading_path") or "").strip()
        if heading and heading not in headings:
            headings.append(heading)
        if len(excerpts) < max_excerpts:
            excerpts.append(_strip_heading_lines(chunk["text"])[:200])

    score = max(
        float(chunk.get("rerank_score", chunk.get("score", 0.0)) or 0.0)
        for _, chunk in members
    )
    rep_heading = str(first_meta.get("heading_path") or "").strip()

    return {
        "index": first_idx,
        "indices": [idx for idx, _ in members],
        "chunk_count": len(members),
        "title": title,
        "file_path": first_meta.get("file_path", "未知"),
        "heading_path": rep_heading or (headings[0] if headings else ""),
        "headings": headings,
        "source_type": first_meta.get("source_type", ""),
        "source_url": first_meta.get("source_url", ""),
        "excerpt": excerpts[0] if excerpts else "",
        "excerpts": excerpts,
        "score": score,
    }


def merge_chunks_to_sources(chunks: list[dict], max_excerpts: int = MAX_EXCERPTS) -> list[dict]:
    """把同一文档的多个检索片段合并为一张来源卡片（V1.2 + V1.3）。

    两轮合并：
    1. 按 (source_type, 归一化路径) 分组 —— 解决同一文件路径表示不一致导致的漏合并。
    2. 跨 source_type 标题合并（hybrid）—— personal_note 与 tech_doc 标题相同
       时合并为一张卡，优先本地，同时保留本地与网页两个跳转入口。

    关键约束：indices 必须完整保留片段序号（1-based），因为正文里的 [来源N]
    直接对应送进 LLM context 的片段序号；只合并展示层，不改 context 顺序与编号。
    """
    # 第 1 轮：按路径分组
    groups: dict[str, list[tuple[int, dict]]] = {}
    for i, chunk in enumerate(chunks):
        idx = i + 1
        meta = chunk.get("metadata") or {}
        groups.setdefault(_group_key(idx, meta), []).append((idx, chunk))

    for members in groups.values():
        members.sort(key=lambda m: m[0])

    prototypes = [_build_card(members, max_excerpts) for members in groups.values()]

    # 第 2 轮：personal_note + tech_doc 同标题 → hybrid
    tech_by_title: dict[str, dict] = {}
    for proto in prototypes:
        if proto["source_type"] == "tech_doc":
            nt = _normalize_title(proto["title"])
            if nt and nt not in tech_by_title:
                tech_by_title[nt] = proto

    consumed_tech: set[int] = set()
    cards: list[dict] = []
    for proto in prototypes:
        if proto["source_type"] != "personal_note":
            continue
        nt = _normalize_title(proto["title"])
        tech = tech_by_title.get(nt)
        if tech is not None and id(tech) not in consumed_tech:
            consumed_tech.add(id(tech))
            all_indices = sorted(set(proto["indices"]) | set(tech["indices"]))
            merged_excerpts = (proto["excerpts"] + tech["excerpts"])[:max_excerpts]
            merged_headings: list[str] = []
            for h in proto["headings"] + tech["headings"]:
                if h not in merged_headings:
                    merged_headings.append(h)
            cards.append(
                {
                    "index": min(proto["index"], tech["index"]),
                    "indices": all_indices,
                    "chunk_count": proto["chunk_count"] + tech["chunk_count"],
                    "title": proto["title"] or tech["title"],
                    "file_path": proto["file_path"],  # 本地优先
                    "heading_path": proto["heading_path"] or tech["heading_path"],
                    "headings": merged_headings,
                    "source_type": "personal_note",  # 本地优先
                    "source_url": tech["source_url"],  # 网页 URL
                    "is_hybrid": True,
                    "excerpt": merged_excerpts[0] if merged_excerpts else "",
                    "excerpts": merged_excerpts,
                    "score": max(proto["score"], tech["score"]),
                }
            )
        else:
            cards.append(proto)

    # 未被 hybrid 消耗的 tech_doc 与其他类型
    for proto in prototypes:
        if proto["source_type"] == "tech_doc" and id(proto) not in consumed_tech:
            cards.append(proto)
        elif proto["source_type"] not in ("personal_note", "tech_doc"):
            cards.append(proto)

    cards.sort(key=lambda card: (-card["score"], card["index"]))
    return cards


def build_sources(chunks: list[dict]) -> list[dict]:
    """按文档合并后的来源卡片列表（V1.2）。"""
    return merge_chunks_to_sources(chunks)


async def chat_flow(
    state,
    question: str,
    session_id: str,
    top_k: int = 5,
    model_id: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """主对话流程，yield (event_type, data)。"""
    session_store = state.session_store
    history = session_store.get_history(session_id, limit=REWRITE_HISTORY_TURNS * 2)

    # 0. meta
    yield "meta", {
        "model": state.gateway.current_model or "auto",
        "provider": state.gateway.current_provider or "auto",
        "task_type": "generate",
    }

    # 0.5 敏感/越狱问题优先拒答，避免被低提问分误入人工确认流程
    if classify_fallback(question) == "sensitive":
        preamble = build_fallback_preamble("sensitive")
        yield "content", {"text": preamble}
        session_store.add_message(session_id, "user", question)
        session_store.add_message(session_id, "assistant", preamble)
        yield "done", {"session_id": session_id, "fallback": "sensitive"}
        return

    # 1. 提问信息评分
    q_score = state.question_scorer.score(question, history=history)
    session_store.add_score_log(session_id, question, q_score=q_score, r_score=None,
                                level=None, confidence=None, model=None)

    if q_score < 60:
        # 低分问题需要给出“理解后的改写”供确认；即使无历史也强制走改写模型
        rewritten = await rewrite_query(state.gateway, question, history, force_llm=True)
        yield "confirm", {
            "original_question": question,
            "rewritten_question": rewritten,
            "options": ["确认", "修改", "记录此问题"],
        }
        return

    # 2. 问题改写
    rewritten = await rewrite_query(state.gateway, question, history)

    # 3. 检索策略评分定级
    r_score = state.retrieval_scorer.score(rewritten)
    level = decide_level(r_score)

    # 4. 带降级的分级检索
    chunks, used_level, confidence = state.retriever.retrieve_with_fallback(
        rewritten, level=level, top_k=top_k
    )
    session_store.add_score_log(
        session_id, rewritten, q_score=q_score, r_score=r_score,
        level=used_level, confidence=confidence,
        model=state.gateway.current_model,
    )

    # 5. 兜底
    if not chunks or confidence < 0.3:
        kind = classify_fallback(question)
        preamble = build_fallback_preamble(kind)
        if kind == "general":
            yield "content", {"text": DISCLAIMER + "\n\n"}
            answer = await _llm_answer_without_context(state, rewritten, model_id)
            async for token in _stream_text(answer):
                yield "content", {"text": token}
        else:
            yield "content", {"text": preamble}
        session_store.add_message(session_id, "user", question)
        session_store.add_message(session_id, "assistant", preamble)
        yield "done", {"session_id": session_id, "fallback": kind}
        return

    # 5.5 中置信（0.3~0.6）但属通用常识问题：检索结果实际不相关，
    # 不能把无关片段当来源，改为“未基于本地知识库”声明后自答
    if confidence < CONFIDENCE_HIGH and classify_fallback(question) == "general":
        yield "content", {"text": DISCLAIMER + "\n\n"}
        answer = await _llm_answer_without_context(state, rewritten, model_id)
        async for token in _stream_text(answer):
            yield "content", {"text": token}
        session_store.add_message(session_id, "user", question)
        session_store.add_message(session_id, "assistant", DISCLAIMER + "\n\n" + answer)
        yield "done", {"session_id": session_id, "fallback": "general", "confidence": confidence}
        return

    # 6. sources
    sources = build_sources(chunks)
    yield "sources", sources

    # 7. 流式生成（中置信附加提示）
    if confidence < CONFIDENCE_HIGH:
        yield "content", {"text": INCOMPLETE_NOTICE + "\n\n"}

    answer_parts: list[str] = []
    async for token in _generate_streaming(state, rewritten, chunks, model_id):
        answer_parts.append(token)
        yield "content", {"text": token}

    session_store.add_message(session_id, "user", question)
    session_store.add_message(session_id, "assistant", "".join(answer_parts), sources=sources,
                              model=state.gateway.current_model)
    yield "done", {"session_id": session_id, "level": used_level, "confidence": confidence}


async def _stream_text(text: str) -> AsyncIterator[str]:
    # 非流式兜底回答按小块"打字机"输出
    step = 24
    for i in range(0, len(text), step):
        yield text[i : i + step]


async def _llm_answer_without_context(state, question: str, model_id: str | None) -> str:
    """通用技术常识的声明式自答（无知识库上下文）。"""
    try:
        answer = await state.gateway.chat(
            messages=[{"role": "user", "content": question}],
            task_type="generate",
            model_id=model_id,
        )
        return answer
    except Exception as exc:  # noqa: BLE001
        logger.warning("兜底自答失败: %s", exc)
        return "（模型服务暂不可用，请稍后再试）"


async def _generate_streaming(state, query: str, chunks: list[dict], model_id: str | None):
    """带知识库上下文的流式生成。"""
    context = build_context(chunks)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(context=context)},
        {"role": "user", "content": query},
    ]
    # 生成异常不向内容流拼接错误文本，交给 SSE 顶层产出 error 事件
    stream = await state.gateway.chat(messages, task_type="generate", stream=True,
                                      model_id=model_id)
    async for token in stream:
        yield token
