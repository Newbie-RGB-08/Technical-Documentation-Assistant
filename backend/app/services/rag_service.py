"""RAG 服务编排（PRD 3.5.2 主流程）。

提问评分 →（<60 走 confirm 终止）→ 问题改写 → 检索评分定级 → 降级检索
→ 置信度判定 →（兜底）→ sources → 流式生成 → done。
产出 (event_type, data) 元组流，由路由层格式化为 SSE。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import AsyncIterator

from app.config import get_settings
from app.core.complexity_router import (
    classify_complexity,
    route_for_complexity,
    score_complexity,
)
from app.core.confidence import (
    INCOMPLETE_NOTICE,
    build_fallback_preamble,
    classify_fallback,
)
from app.core.evidence import (
    Answerability,
    build_evidence_pack,
    fit_context_chunks,
)
from app.core.fallback_ladder import FallbackLevel, fallback_message
from app.core.input_guard import validate_question
from app.core.query_rewriter import rewrite_query
from app.core.retrieval_scorer import RetrievalScorer, decide_level
from app.core.question_scorer import QuestionScorer
from app.services.answer_pipeline import (
    AnswerResult,
    classify_specificity,
    run_general_stream,
    run_strict_pipeline,
)
from app.services.retrieval_orchestrator import orchestrate
from app.services.web_writeback import normalize_url

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


def _selected_meta(gateway) -> dict | None:
    """meta.selected（V1.5.x 可选字段）：当前模型选择与 auto 标记。

    优先最近一次成功选择（含自动绑定），其次启动期预选；任何异常都返回 None，
    保证旧调用方（测试注入的假网关）不受影响。
    """
    getter = getattr(gateway, "selected_payload", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001
        return None


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
            excerpt = _strip_heading_lines(chunk["text"])[:200]
            if excerpt:
                excerpts.append(excerpt)

    score = max(
        float(chunk.get("rerank_score", chunk.get("score", 0.0)) or 0.0)
        for _, chunk in members
    )
    rep_heading = str(first_meta.get("heading_path") or "").strip()
    origin = str(first_meta.get("origin") or "").strip()
    if not origin:
        origin = "web_search" if first_meta.get("source_url") else "local"
    trust_level = str(first_meta.get("trust_level") or "").strip()
    if not trust_level:
        trust_level = "web_medium" if origin == "web_search" else "local_high"

    card = {
        "index": first_idx,
        "indices": [idx for idx, _ in members],
        "chunk_count": len(members),
        "title": title,
        "file_path": first_meta.get("file_path", "未知"),
        "heading_path": rep_heading or (headings[0] if headings else ""),
        "headings": headings,
        "source_type": first_meta.get("source_type", ""),
        "source_url": first_meta.get("source_url", ""),
        "origin": origin,
        "retrieved_at": first_meta.get("retrieved_at", ""),
        "trust_level": trust_level,
        "excerpt": excerpts[0] if excerpts else "",
        "excerpts": excerpts,
        "score": score,
    }
    # 上传字段只在上传来源卡出现，旧来源卡的键集合保持不变（兼容旧前端/测试）。
    if (
        card["source_type"] == "uploaded_doc"
        or first_meta.get("upload_id")
        or first_meta.get("doc_format")
        or first_meta.get("original_filename")
    ):
        card.update(
            {
                "upload_id": first_meta.get("upload_id") or None,
                "doc_format": first_meta.get("doc_format") or None,
                "original_filename": first_meta.get("original_filename") or None,
            }
        )
    return card


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
                    "origin": "hybrid",
                    "retrieved_at": tech.get("retrieved_at", ""),
                    "trust_level": "local_high",
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


def _record_trace(state, trace: dict, started: float) -> None:
    store = getattr(state, "session_store", None)
    if store is None or not hasattr(store, "add_retrieval_trace"):
        return
    try:
        store.add_retrieval_trace(
            trace.get("session_id") or "",
            request_id=trace.get("request_id"),
            question=trace.get("question") or "",
            complexity=trace.get("complexity"),
            route=trace.get("route"),
            local_level=trace.get("local_level"),
            web_used=bool(trace.get("web_used")),
            web_urls=list(trace.get("web_urls") or []),
            answer_status=trace.get("answer_status"),
            verified=bool(trace.get("verified")),
            fallback_level=trace.get("fallback_level"),
            kb_written=bool(trace.get("kb_written")),
            elapsed_ms=trace.get("elapsed_ms") or _elapsed_ms(started),
            interrupted=bool(trace.get("interrupted")),
            error=trace.get("error"),
        )
    except Exception as exc:  # noqa: BLE001 - trace 失败不影响回答
        logger.warning("记录检索 trace 失败: %s", exc)


async def chat_flow(
    state,
    question: str,
    session_id: str,
    top_k: int = 5,
    model_id: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """主对话流程：生成 request_id、记录 trace，并在中断时标记 interrupted。"""
    request_id = uuid.uuid4().hex
    started = time.monotonic()
    trace = {
        "session_id": session_id,
        "request_id": request_id,
        "question": str(question or ""),
        "complexity": None,
        "route": None,
        "local_level": None,
        "web_used": False,
        "web_urls": [],
        "answer_status": "pending",
        "verified": False,
        "fallback_level": None,
        "kb_written": False,
        "elapsed_ms": 0,
        "interrupted": False,
        "error": None,
    }

    try:
        async for event_type, data in _chat_flow_inner(
            state,
            question=question,
            session_id=session_id,
            top_k=top_k,
            model_id=model_id,
            request_id=request_id,
            trace=trace,
        ):
            if event_type == "meta":
                if data.get("complexity"):
                    trace["complexity"] = data["complexity"]
                if data.get("route"):
                    trace["route"] = data["route"]
                data.setdefault("request_id", request_id)
            elif event_type == "status":
                if data.get("stage") == "local_retrieval" and data.get("level") is not None:
                    trace["local_level"] = int(data["level"])
            elif event_type == "sources":
                urls = [
                    str(card.get("source_url"))
                    for card in data
                    if isinstance(card, dict) and card.get("source_url")
                ]
                if urls:
                    trace["web_urls"] = urls
                    trace["web_used"] = True
            elif event_type == "done":
                trace["answer_status"] = data.get("answer_status") or trace["answer_status"]
                trace["verified"] = bool(data.get("verified"))
                trace["fallback_level"] = data.get("fallback_level")
                trace["kb_written"] = bool(data.get("kb_written"))
                trace["elapsed_ms"] = data.get("elapsed_ms") or _elapsed_ms(started)
                data.setdefault("request_id", request_id)
                data.setdefault("interrupted", False)
            yield event_type, data
    except (asyncio.CancelledError, GeneratorExit):
        trace["interrupted"] = True
        if trace["answer_status"] in ("pending", None):
            trace["answer_status"] = "interrupted"
        raise
    except Exception as exc:  # noqa: BLE001 - 上层路由会转 error 事件
        trace["error"] = str(exc)
        if trace["answer_status"] in ("pending", None):
            trace["answer_status"] = "error"
        raise
    finally:
        _record_trace(state, trace, started)


async def _chat_flow_inner(
    state,
    *,
    question: str,
    session_id: str,
    top_k: int,
    model_id: str | None,
    request_id: str,
    trace: dict,
) -> AsyncIterator[tuple[str, dict]]:
    """主对话流程，yield (event_type, data)。"""
    settings = get_settings()
    started = time.monotonic()
    question = str(question or "")
    session_store = state.session_store
    history = session_store.get_history(
        session_id,
        limit=REWRITE_HISTORY_TURNS * 2,
        verified_only=settings.session_history_verified_only,
    )

    # 0. 输入防线：空/超长问题直接给出结构化错误，不进入检索
    input_check = validate_question(question, settings.max_question_chars)
    if not input_check.ok:
        yield "error", {"code": input_check.code, "message": input_check.message,
                        "suggestion": "请修改问题后重试。"}
        yield "done", {"session_id": session_id, "answer_status": "refused",
                       "elapsed_ms": _elapsed_ms(started)}
        return

    # 0.1 meta（拿到复杂度后再补一次完整 meta）
    yield "meta", {
        "model": state.gateway.current_model or "auto",
        "provider": state.gateway.current_provider or "auto",
        "selected": _selected_meta(state.gateway),
        "task_type": "generate",
        "complexity": None,
        "route": None,
        "answer_mode": settings.answer_mode,
        "web_enabled": settings.web_search_enabled,
        "request_id": request_id,
    }

    # 0.5 敏感/越狱问题优先拒答，避免被低提问分误入人工确认流程
    if classify_fallback(question) == "sensitive":
        preamble = build_fallback_preamble("sensitive")
        yield "content", {"text": preamble}
        session_store.add_message(session_id, "user", question, request_id=request_id)
        session_store.add_message(
            session_id, "assistant", preamble,
            answer_status="refused", request_id=request_id,
        )
        yield "done", {"session_id": session_id, "fallback": "sensitive",
                       "answer_status": "refused", "elapsed_ms": _elapsed_ms(started)}
        return

    # 1. 提问信息评分
    yield "status", {"stage": "scoring"}
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
        trace["answer_status"] = "confirm"
        return

    # 2. 问题改写
    rewritten = await rewrite_query(state.gateway, question, history)

    # 3. 复杂度评分与检索路由（PRD E.3）
    r_score = state.retrieval_scorer.score(rewritten)
    keyword_weights = getattr(state.retrieval_scorer, "keyword_weights", {})
    complexity_score = score_complexity(rewritten, keyword_weights, history)
    complexity = classify_complexity(complexity_score)
    route = route_for_complexity(complexity, r_score)
    level = max(route.local_level, decide_level(r_score))

    yield "meta", {
        "model": state.gateway.current_model or "auto",
        "provider": state.gateway.current_provider or "auto",
        "selected": _selected_meta(state.gateway),
        "task_type": "generate",
        "complexity": complexity,
        "complexity_score": complexity_score,
        "route": route.web_strategy,
        "answer_mode": settings.answer_mode,
        "web_enabled": settings.web_search_enabled,
        "request_id": request_id,
    }
    yield "status", {"stage": "local_retrieval", "complexity": complexity, "level": level}

    # 4. 检索编排：本地 / 线上（复杂题并行，简单/中等未命中时升级）；写回在 P4 接入
    web_gateway = getattr(state, "web_gateway", None)
    web_fetcher = getattr(state, "web_fetcher", None)
    if route.web_strategy == "parallel" and web_gateway is not None:
        yield "status", {"stage": "web_searching"}

    outcome = await orchestrate(
        rewritten,
        local_retriever=state.retriever.retrieve_with_fallback,
        complexity=complexity,
        route=route.web_strategy,
        top_k=top_k,
        level=level,
        web_gateway=web_gateway,
        fetcher=web_fetcher,
        reranker=getattr(state.retriever, "reranker", None),
    )
    chunks = outcome.chunks
    used_level = outcome.level
    confidence = outcome.confidence
    if outcome.web_used:
        yield "status", {"stage": "web_fetching", "web_chunks": len(outcome.web_chunks)}

    session_store.add_score_log(
        session_id, rewritten, q_score=q_score, r_score=r_score,
        level=used_level, confidence=confidence,
        model=state.gateway.current_model,
    )

    # 5. 证据充足性门（PRD E.5）
    answerability = outcome.answerability

    if answerability in (Answerability.HIT, Answerability.PARTIAL):
        async for event in _strict_answer(
            state, settings=settings, question=question, rewritten=rewritten,
            chunks=chunks, answerability=answerability, session_id=session_id,
            model_id=model_id, used_level=used_level, confidence=confidence,
            started=started, pages=outcome.pages, request_id=request_id,
        ):
            yield event
        return

    if answerability == Answerability.STALE:
        # 线上可用时由 P3 编排刷新；当前先显式提示时效并走严格管道
        async for event in _strict_answer(
            state, settings=settings, question=question, rewritten=rewritten,
            chunks=chunks, answerability=answerability, session_id=session_id,
            model_id=model_id, used_level=used_level, confidence=confidence,
            started=started, stale_notice=True, pages=outcome.pages,
            request_id=request_id,
        ):
            yield event
        return

    # 6. 本地未命中：按问题具体性决定通用流式或明确未找到（PRD E.4）
    async for event in _no_evidence_answer(
        state, settings=settings, question=question, rewritten=rewritten,
        session_id=session_id, model_id=model_id, started=started,
        request_id=request_id,
    ):
        yield event


def _cited_web_pages(
    result: AnswerResult,
    evidence,
    chunks: list[dict],
    pages: list,
) -> list[tuple[object, float]]:
    """严格管道实际引用（含摘录兜底）且属于线上的网页，按首次引用去重。"""
    if not pages or not result.used_indices:
        return []
    page_map: dict[str, object] = {}
    for page in pages:
        page_map[page.url] = page
        canonical = normalize_url(page.url)
        if canonical:
            page_map[canonical] = page

    cited: list[tuple[object, float]] = []
    seen: set[int] = set()
    for index in result.used_indices:
        item = evidence.get(index)
        if item is None or item.origin != "web_search":
            continue
        url = item.source_url
        page = page_map.get(url) or page_map.get(normalize_url(url))
        if page is None or id(page) in seen:
            continue
        chunk = chunks[index - 1] if 0 < index <= len(chunks) else {}
        relevance = float(chunk.get("score", 0.0) or 0.0)
        seen.add(id(page))
        cited.append((page, relevance))
    return cited


async def _strict_answer(
    state,
    *,
    settings,
    question: str,
    rewritten: str,
    chunks: list[dict],
    answerability: Answerability,
    session_id: str,
    model_id: str | None,
    used_level: int,
    confidence: float,
    started: float,
    stale_notice: bool = False,
    pages: list | None = None,
    request_id: str = "",
) -> AsyncIterator[tuple[str, dict]]:
    """有证据时的严格管道：缓冲生成 → 校验 → 修复 → 摘录 → 打字机输出。"""
    chunks = fit_context_chunks(chunks, settings.max_context_chars)
    sources = build_sources(chunks)
    yield "sources", sources

    yield "status", {"stage": "generating", "answerability": answerability.value}
    yield "status", {"stage": "verifying"}
    evidence = build_evidence_pack(chunks)

    # 管道内部可能触发一次修复；用状态队列在等待期间实时透出 repairing
    status_queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(
        run_strict_pipeline(
            state.gateway, rewritten, evidence, model_id,
            on_status=status_queue.put_nowait,
        )
    )
    getter = asyncio.create_task(status_queue.get())
    try:
        while True:
            done, _pending = await asyncio.wait(
                {task, getter}, return_when=asyncio.FIRST_COMPLETED
            )
            if getter in done:
                yield "status", {"stage": getter.result()}
                getter = asyncio.create_task(status_queue.get())
            if task in done:
                break
        result = task.result()
    except asyncio.CancelledError:
        task.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - 工具异常不暴露原始堆栈
        logger.warning("严格管道异常，降级为未找到: %s", exc)
        result = AnswerResult(
            text=fallback_message(FallbackLevel.L4_REFUSED),
            status="refused",
            fallback_level=int(FallbackLevel.L4_REFUSED),
        )
    finally:
        if not getter.done():
            getter.cancel()

    if stale_notice:
        yield "content", {"text": "以下资料可能已超过时效，请结合官方文档核对。\n\n"}
    elif answerability == Answerability.PARTIAL:
        yield "content", {"text": INCOMPLETE_NOTICE + "\n\n"}

    async for token in _stream_text(result.text):
        yield "content", {"text": token}

    state.session_store.add_message(session_id, "user", question, request_id=request_id)
    state.session_store.add_message(
        session_id, "assistant", result.text, sources=sources,
        model=state.gateway.current_model,
        answer_status=result.status, request_id=request_id,
    )

    # 实际引用且通过质量门的网页投入后台写回队列，不阻塞首 Token
    kb_written = False
    cited = _cited_web_pages(result, evidence, chunks, pages or [])
    queue = getattr(state, "writeback_queue", None)
    if cited and queue is not None:
        yield "status", {"stage": "writing_back", "pages": len(cited)}
        limit = max(0, int(settings.web_writeback_max_pages_per_turn))
        for page, relevance in cited[:limit]:
            if queue.put({"page": page, "query": rewritten, "relevance": relevance}):
                kb_written = True

    # 回答正文实际引用的证据编号（1..N），前端据此标记来源“已引用/未引用”
    used_indices = sorted({i for i in result.used_indices if 1 <= i <= len(chunks)})
    yield "done", {
        "session_id": session_id,
        "level": used_level,
        "confidence": confidence,
        "answer_status": result.status,
        "verified": result.verified,
        "fallback_level": result.fallback_level,
        "used_indices": used_indices,
        "verification": result.verification,
        "kb_written": kb_written,
        "elapsed_ms": _elapsed_ms(started),
    }


async def _no_evidence_answer(
    state,
    *,
    settings,
    question: str,
    rewritten: str,
    session_id: str,
    model_id: str | None,
    started: float,
    request_id: str = "",
) -> AsyncIterator[tuple[str, dict]]:
    """无本地证据：宽泛概念走通用流式，具体问题禁止自答。"""
    specificity = classify_specificity(rewritten)
    general_allowed = (
        settings.general_stream_enabled
        and settings.answer_mode != "strict"
        and (
            not settings.general_stream_requires_concept_only
            or specificity == "concept"
        )
    )

    if general_allowed:
        yield "status", {"stage": "general_streaming", "specificity": specificity}
        result: AnswerResult | None = None
        async for item in run_general_stream(state.gateway, rewritten, model_id):
            if item["event"] == "token":
                yield "content", {"text": item["text"]}
            else:
                result = item["result"]
        if result is None:  # pragma: no cover - 生成器契约保证有 done
            result = AnswerResult(
                text=fallback_message(FallbackLevel.L4_REFUSED),
                status="refused",
                fallback_level=int(FallbackLevel.L4_REFUSED),
            )
        state.session_store.add_message(session_id, "user", question, request_id=request_id)
        state.session_store.add_message(
            session_id, "assistant", result.text, model=state.gateway.current_model,
            answer_status=result.status, request_id=request_id,
        )
        yield "done", {
            "session_id": session_id,
            "answer_status": result.status,
            "verified": False,
            "fallback_level": result.fallback_level,
            "verification": {"mode": "general", "verified": False},
            "kb_written": False,
            "elapsed_ms": _elapsed_ms(started),
        }
        return

    # 具体 API/版本/错误码/命令问题：禁止通用自答
    text = fallback_message(FallbackLevel.L4_REFUSED)
    yield "content", {"text": text}
    state.session_store.add_message(session_id, "user", question, request_id=request_id)
    state.session_store.add_message(
        session_id, "assistant", text, model=state.gateway.current_model,
        answer_status="refused", request_id=request_id,
    )
    yield "done", {
        "session_id": session_id,
        "answer_status": "refused",
        "verified": False,
        "fallback_level": int(FallbackLevel.L4_REFUSED),
        "verification": {"mode": "refused", "reason": "no_evidence"},
        "kb_written": False,
        "elapsed_ms": _elapsed_ms(started),
    }


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def _stream_text(text: str) -> AsyncIterator[str]:
    # 严格管道校验通过后按小块"打字机"输出
    step = 24
    for i in range(0, len(text), step):
        yield text[i : i + step]
