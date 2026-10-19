"""RAG 服务编排（PRD 3.5.2 主流程）。

提问评分 →（<60 走 confirm 终止）→ 问题改写 → 检索评分定级 → 降级检索
→ 置信度判定 →（兜底）→ sources → 流式生成 → done。
产出 (event_type, data) 元组流，由路由层格式化为 SSE。
"""
from __future__ import annotations

import logging
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
        parts.append(
            f"[来源{i + 1}] 文件：{meta.get('file_path', '未知')} | "
            f"章节：{meta.get('heading_path', '')}\n{c['text']}"
        )
    return "\n\n".join(parts)


def build_sources(chunks: list[dict]) -> list[dict]:
    return [
        {
            "index": i + 1,
            "file_path": c.get("metadata", {}).get("file_path", "未知"),
            "heading_path": c.get("metadata", {}).get("heading_path", ""),
            "excerpt": c["text"][:200],
            "score": float(c.get("rerank_score", c.get("score", 0.0)) or 0.0),
        }
        for i, c in enumerate(chunks)
    ]


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
