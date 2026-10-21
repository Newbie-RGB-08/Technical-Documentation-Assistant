"""检索编排：本地 / 线上并行、统一重排、配额与连续编号（PRD E.11）。

- concise：仅本地。
- fallback：本地未命中（MISS/STALE）时升级一次线上。
- parallel：本地与线上同时执行。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config import get_settings
from app.core.evidence import Answerability, judge_answerability
from app.services.web_fetcher import FetchedPage

logger = logging.getLogger(__name__)

WEB_CHUNK_MAX_CHARS = 1200
WEB_CANDIDATE_MAX = 6
WEB_PARAGRAPHS_PER_PAGE = 2
MIN_PARAGRAPH_CHARS = 60


@dataclass
class RetrievalOutcome:
    chunks: list[dict]
    level: int
    confidence: float
    local_chunks: list[dict] = field(default_factory=list)
    web_chunks: list[dict] = field(default_factory=list)
    pages: list[FetchedPage] = field(default_factory=list)
    web_used: bool = False
    answerability: Answerability = Answerability.MISS


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", text)}


def extract_paragraphs(
    markdown: str,
    query: str,
    *,
    max_n: int = WEB_PARAGRAPHS_PER_PAGE,
    max_chars: int = WEB_CHUNK_MAX_CHARS,
) -> list[str]:
    """从网页正文挑选与问题最相关的段落。"""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", markdown or "") if b.strip()]
    query_tokens = _tokens(query)
    scored: list[tuple[float, int, str]] = []
    for position, block in enumerate(blocks):
        text = block.strip()
        if len(text) < MIN_PARAGRAPH_CHARS:
            continue
        overlap = len(query_tokens & _tokens(text))
        scored.append((overlap, -position, text[:max_chars]))
    if not scored:
        # Fallback：全段落都很短时放宽到 20 字符 + 至少一个 query token 重叠
        for position, block in enumerate(blocks):
            text = block.strip()
            if len(text) < 20:
                continue
            overlap = len(query_tokens & _tokens(text))
            if overlap > 0:
                scored.append((overlap, -position, text[:max_chars]))
    if not scored:
        return []
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [text for _overlap, _pos, text in scored[:max_n]]


def _doc_key(chunk: dict) -> str:
    meta = chunk.get("metadata") or {}
    if meta.get("origin") == "web_search":
        return f"web|{meta.get('canonical_url') or meta.get('source_url') or ''}"
    return f"local|{meta.get('file_path') or meta.get('title') or chunk.get('id')}"


def build_web_chunks(page: FetchedPage, query: str) -> list[dict]:
    paragraphs = extract_paragraphs(page.markdown, query)
    if not paragraphs:
        return []
    parsed = urlparse(page.url)
    host = (parsed.hostname or "web").lower()
    url_hash = hashlib.sha256(
        (page.url or "").encode("utf-8", errors="ignore")
    ).hexdigest()[:8]
    file_path = f"data/tech_docs/web/{host}-{url_hash}.md"
    return [
        {
            "id": f"web:{page.url}#{i}",
            "text": text,
            "metadata": {
                "file_path": file_path,
                "source_type": "tech_doc",
                "origin": "web_search",
                "source_url": page.url,
                "canonical_url": page.url,
                "title": page.title,
                "heading_path": "网页正文",
                "retrieved_at": page.retrieved_at,
                "search_query": query,
                "provider": page.provider,
                "trust_level": "web_medium",
            },
            "score": 0.7,
        }
        for i, text in enumerate(paragraphs)
    ]


def _norm_text(text: str) -> str:
    """文本归一化：压缩全部空白，用于识别换行/缩进不同但内容相同的重复片段。"""
    return re.sub(r"\s+", "", str(text or ""))


def _canonical_doc_path(path) -> str:
    """文档路径归一化：绝对路径与相对路径指向同一文件时返回同一个键。"""
    p = str(path or "").strip().replace("\\", "/")
    idx = p.rfind("/data/")
    if idx != -1:
        p = p[idx + len("/data/"):]
    elif p.startswith("data/"):
        p = p[len("data/"):]
    return p.strip("/").casefold()


def _dedupe(chunks: list[dict]) -> list[dict]:
    """按 URL / 归一化文本 / 同文档同章节前缀去重，避免入库路径差异造成重复来源。"""
    seen_urls: set[str] = set()
    seen_texts: set[str] = set()
    seen_prefixes: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        url = str(meta.get("canonical_url") or meta.get("source_url") or "").strip()
        text = _norm_text(chunk.get("text") or "")
        doc_path = _canonical_doc_path(meta.get("file_path"))
        heading = str(meta.get("heading_path") or "").strip()
        prefix = text[:80]
        prefix_key = (doc_path, heading, prefix)
        if url and url in seen_urls:
            continue
        if text and text in seen_texts:
            continue
        if doc_path and prefix and heading and prefix_key in seen_prefixes:
            continue
        if url:
            seen_urls.add(url)
        if text:
            seen_texts.add(text)
        if doc_path and prefix and heading:
            seen_prefixes.add(prefix_key)
        out.append(chunk)
    return out


def _relevance_score(chunk: dict) -> float:
    """片段相关性：优先 rerank 分，其次向量相似度，最后融合/网页分。"""
    if chunk.get("rerank_score") is not None:
        return float(chunk["rerank_score"])
    if chunk.get("vector_score") is not None:
        return float(chunk["vector_score"])
    return float(chunk.get("score", 0.0) or 0.0)


def _filter_relevant(
    chunks: list[dict], *, min_score: float, relative: float
) -> list[dict]:
    """过滤明显无关的尾部片段；至少保留最相关的一条。"""
    if not chunks:
        return []
    top = max(_relevance_score(chunk) for chunk in chunks)
    floor = max(min_score, top * relative)
    kept = [chunk for chunk in chunks if _relevance_score(chunk) >= floor]
    return kept or chunks[:1]


def _apply_quota(
    candidates: list[dict],
    *,
    total: int,
    local_limit: int = 5,
    web_limit: int = 3,
    per_doc_limit: int = 2,
) -> list[dict]:
    selected: list[dict] = []
    per_doc: dict[str, int] = {}
    local_count = web_count = 0
    for chunk in candidates:
        if len(selected) >= total:
            break
        is_web = (chunk.get("metadata") or {}).get("origin") == "web_search"
        if is_web and web_count >= web_limit:
            continue
        if not is_web and local_count >= local_limit:
            continue
        key = _doc_key(chunk)
        if per_doc.get(key, 0) >= per_doc_limit:
            continue
        per_doc[key] = per_doc.get(key, 0) + 1
        if is_web:
            web_count += 1
        else:
            local_count += 1
        selected.append(chunk)
    return selected


def _rank(chunks: list[dict], query: str, reranker) -> list[dict]:
    """统一重排；Reranker 不可用时按本地 RRF / 线上排名的等权融合排序。"""
    if not chunks:
        return []
    if reranker is not None:
        try:
            scores = reranker.rerank(query, [str(c.get("text") or "") for c in chunks])
            for chunk, score in zip(chunks, scores):
                chunk["rerank_score"] = float(score)
            return sorted(chunks, key=lambda c: c.get("rerank_score", 0.0), reverse=True)
        except Exception as exc:  # noqa: BLE001 - 降级 RRF
            logger.warning("Reranker 不可用，降级 RRF 融合: %s", exc)

    def fused(chunk: dict) -> float:
        is_web = (chunk.get("metadata") or {}).get("origin") == "web_search"
        # 优先已有 rerank_score（本地 Retriever 可能已经给了），否则用原始 score
        if "rerank_score" in chunk:
            base = float(chunk["rerank_score"])
        else:
            base = float(chunk.get("score", 0.0) or 0.0)
        return base * (0.7 if is_web else 1.0)

    # 降级路径只用于排序，不能把 RRF 分（约 0.01）写成 rerank_score：
    # 否则 judge_answerability 会把真实本地命中误判为 MISS（E.5 回归）。
    return sorted(chunks, key=fused, reverse=True)


async def _web_candidates(query: str, gateway, fetcher) -> tuple[list[dict], list[FetchedPage]]:
    if gateway is None or fetcher is None:
        return [], []
    try:
        results = await gateway.search(query)
    except Exception as exc:  # noqa: BLE001 - 线上失败不影响本地
        logger.warning("线上搜索失败，降级本地: %s", exc)
        return [], []
    if not results:
        return [], []
    try:
        pages = await fetcher(results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("网页抓取失败，降级本地: %s", exc)
        return [], []

    chunks: list[dict] = []
    for page in pages:
        chunks.extend(build_web_chunks(page, query))
        if len(chunks) >= WEB_CANDIDATE_MAX:
            break
    return chunks[:WEB_CANDIDATE_MAX], pages


async def orchestrate(
    query: str,
    *,
    local_retriever,
    complexity: str,
    route: str,
    top_k: int = 5,
    level: int | None = None,
    web_gateway=None,
    fetcher=None,
    reranker=None,
) -> RetrievalOutcome:
    """执行检索编排，返回融合后的片段与路由结果。"""
    settings = get_settings()
    total = 6 if complexity == "complex" else int(top_k)
    local_level = level if level is not None else (
        3 if complexity in ("medium", "complex") else 2
    )
    web_allowed = web_gateway is not None and route in ("fallback", "parallel")

    def _local() -> tuple[list[dict], int, float]:
        try:
            return local_retriever(
                query, level=local_level, top_k=settings.local_retrieval_candidate_n
            )
        except Exception as exc:  # noqa: BLE001 - 本地失败仍可尝试线上
            logger.warning("本地检索失败: %s", exc)
            return [], -1, 0.0

    local_chunks: list[dict] = []
    web_chunks: list[dict] = []
    pages: list[FetchedPage] = []
    used_level = -1
    confidence = 0.0

    if route == "parallel" and web_allowed:
        local_task = asyncio.to_thread(_local)
        web_task = asyncio.create_task(_web_candidates(query, web_gateway, fetcher))
        local_chunks, used_level, confidence = await local_task
        web_chunks, pages = await web_task
    else:
        local_chunks, used_level, confidence = await asyncio.to_thread(_local)
        answerability = judge_answerability(
            local_chunks,
            query,
            threshold=settings.kb_hit_threshold,
            entity_coverage_min=settings.entity_coverage_min,
            stale_days=settings.stale_doc_days,
            confidence=confidence,
        )
        if web_allowed and answerability in (Answerability.MISS, Answerability.STALE):
            web_chunks, pages = await _web_candidates(query, web_gateway, fetcher)

    merged = _dedupe(list(local_chunks) + list(web_chunks))
    ranked = _rank(merged, query, reranker)
    relevant = _filter_relevant(
        ranked,
        min_score=settings.retrieval_min_score,
        relative=settings.retrieval_relative_score,
    )
    selected = _apply_quota(relevant, total=total)

    answerability = judge_answerability(
        selected,
        query,
        threshold=settings.kb_hit_threshold,
        entity_coverage_min=settings.entity_coverage_min,
        stale_days=settings.stale_doc_days,
        confidence=confidence,
    )

    return RetrievalOutcome(
        chunks=selected,
        level=used_level,
        confidence=confidence,
        local_chunks=list(local_chunks),
        web_chunks=list(web_chunks),
        pages=list(pages),
        web_used=bool(web_chunks),
        answerability=answerability,
    )
