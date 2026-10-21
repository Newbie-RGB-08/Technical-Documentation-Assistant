"""应用状态容器：启动时装配全部单例组件。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import partial

from app.config import get_settings
from app.core.bm25_index import BM25Index
from app.core.question_scorer import QuestionScorer
from app.core.retrieval_scorer import RetrievalScorer, build_keyword_weights
from app.core.retriever import HybridRetriever
from app.providers.local_reranker import LocalBGEReranker
from app.services.llm_gateway import LLMGateway
from app.services.metadata_store import MySQLMetadataStore
from app.services.session_store import SessionStore
from app.services.vector_store import ChromaStore
from app.services.web_cache_store import WebCacheStore
from app.services.web_fetcher import fetch_pages
from app.services.web_search import WebSearchGateway, build_providers
from app.services.web_writeback import WebWriteback, WebWritebackQueue
from app.core.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    chroma: ChromaStore
    metadata: MySQLMetadataStore
    session_store: SessionStore
    bm25: BM25Index
    gateway: LLMGateway
    retriever: HybridRetriever
    question_scorer: QuestionScorer
    retrieval_scorer: RetrievalScorer
    web_gateway: WebSearchGateway | None = None
    web_fetcher: object | None = None
    circuit_breaker: CircuitBreaker | None = None
    web_cache: WebCacheStore | None = None
    web_writeback: WebWriteback | None = None
    writeback_queue: WebWritebackQueue | None = None

    @classmethod
    def build(cls) -> "AppState":
        settings = get_settings()

        chroma = ChromaStore()
        metadata = MySQLMetadataStore(create_all=True)
        session_store = SessionStore()

        bm25 = BM25Index()
        try:
            loaded = bm25.load_from_chroma(chroma)
            logger.info("BM25 索引加载 %s 条", loaded)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BM25 索引加载失败（空库可忽略）: %s", exc)

        gateway = LLMGateway()
        reranker = LocalBGEReranker.instance()
        retriever = HybridRetriever(chroma, bm25, reranker=reranker)

        domain_terms = _load_domain_terms()
        question_scorer = QuestionScorer(domain_terms=domain_terms, session_store=session_store)
        retrieval_scorer = build_retrieval_scorer(metadata)

        circuit_breaker = CircuitBreaker()
        web_cache = WebCacheStore()
        web_gateway: WebSearchGateway | None = None
        web_fetcher = None
        web_writeback: WebWriteback | None = None
        writeback_queue: WebWritebackQueue | None = None
        if settings.web_search_enabled:
            providers = build_providers(settings)
            if providers:
                web_gateway = WebSearchGateway(
                    providers, cache=web_cache, breaker=circuit_breaker
                )
                web_fetcher = partial(fetch_pages, cache=web_cache)
                logger.info(
                    "线上检索就绪：%s",
                    ", ".join(provider.name for provider in providers),
                )

        # 写回后的索引刷新：回调持有 state 引用，构建完成后再注入
        holder: dict = {}

        def _on_ingested(payload: dict) -> None:
            try:
                bm25.load_from_chroma(chroma)
            except Exception as exc:  # noqa: BLE001 - 刷新失败不影响回答
                logger.warning("写回后 BM25 刷新失败: %s", exc)
            current = holder.get("state")
            if current is not None:
                try:
                    current.retrieval_scorer = build_retrieval_scorer(metadata)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("写回后检索评分器刷新失败: %s", exc)
            logger.info("网页写回完成: %s", payload.get("file_path"))

        web_writeback = WebWriteback(
            vector_store=chroma,
            metadata_store=metadata,
            on_ingested=_on_ingested,
        )
        if web_gateway is not None:
            writeback_queue = WebWritebackQueue(web_writeback, auto_start=True)

        state = cls(
            chroma=chroma,
            metadata=metadata,
            session_store=session_store,
            bm25=bm25,
            gateway=gateway,
            retriever=retriever,
            question_scorer=question_scorer,
            retrieval_scorer=retrieval_scorer,
            web_gateway=web_gateway,
            web_fetcher=web_fetcher,
            circuit_breaker=circuit_breaker,
            web_cache=web_cache,
            web_writeback=web_writeback,
            writeback_queue=writeback_queue,
        )
        holder["state"] = state
        return state


def build_retrieval_scorer(metadata: MySQLMetadataStore) -> RetrievalScorer:
    """根据当前知识库元数据重建检索评分器（入库后需刷新关键字权重）。"""
    return RetrievalScorer(build_keyword_weights(
        tags=_collect_tags(metadata),
        titles=[d.get("title") or "" for d in metadata.list_documents()],
        heading_paths=_collect_heading_paths(metadata),
    ))


def _load_domain_terms() -> set[str]:
    settings = get_settings()
    path = settings.data_dir / "domain_terms.json"
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")).keys())
        except Exception:  # noqa: BLE001
            return set()
    return set()


def _collect_tags(metadata: MySQLMetadataStore) -> list[str]:
    try:
        from sqlalchemy import text

        with metadata.engine.connect() as conn:
            return [r[0] for r in conn.execute(text("SELECT DISTINCT name FROM tags"))]
    except Exception:  # noqa: BLE001
        return []


def _collect_heading_paths(metadata: MySQLMetadataStore) -> list[str]:
    try:
        from sqlalchemy import text

        with metadata.engine.connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    text("SELECT DISTINCT heading_path FROM chunks WHERE heading_path IS NOT NULL")
                )
            ]
    except Exception:  # noqa: BLE001
        return []
