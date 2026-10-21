"""全局配置管理（Pydantic Settings）。"""
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# HuggingFace 直连在网络受限环境下会卡死/重试失败，默认走国内镜像（可被外部环境变量覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 路径
    data_dir: Path = BACKEND_DIR / "data"
    chroma_path: Path = BACKEND_DIR / "data" / "chroma_db"
    notes_dir: Path = BACKEND_DIR / "data" / "notes"
    tech_docs_dir: Path = BACKEND_DIR / "data" / "tech_docs"
    models_config_path: Path = BACKEND_DIR / "config" / "models.yaml"
    sqlite_path: Path = BACKEND_DIR / "data" / "sessions.db"

    # MySQL（元数据）
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "rgb123456!@#"
    mysql_database: str = "rag_meta"

    # Ollama（本地 LLM）
    ollama_host: str = "http://localhost:11434"

    # 本地模型
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # 分块参数（PRD 3.2.1）
    chunk_size: int = 512
    chunk_overlap: int = 64

    # 混合检索参数（PRD 3.3.3）
    rrf_k: int = 60
    vector_weight: float = 1.0
    bm25_weight: float = 0.7

    # LLM 调用
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    # ---- V1.4 答案模式与严格校验（PRD 附录 E.14） ----
    answer_mode: str = "hybrid"  # hybrid / strict / stream
    strict_verification_enabled: bool = True
    verification_max_repair_retries: int = 1
    max_unsupported_claim_ratio: float = 0.10
    critical_entity_check: bool = True
    general_stream_enabled: bool = True
    general_stream_requires_concept_only: bool = True
    extractive_fallback_enabled: bool = True
    session_history_verified_only: bool = True

    # ---- 上下文与输入防线 ----
    max_question_chars: int = 4000
    max_context_chars: int = 8000
    max_answer_chars: int = 4000
    request_total_timeout_seconds: float = 15.0
    llm_stage_timeout_seconds: float = 60.0

    # ---- 熔断 ----
    llm_circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown_seconds: float = 60.0

    # ---- 本地命中判定 ----
    kb_hit_threshold: float = 0.45
    entity_coverage_min: float = 0.50
    stale_doc_days: int = 180
    local_retrieval_candidate_n: int = 20

    # ---- 线上检索（PRD 附录 E.9） ----
    web_search_enabled: bool = True
    web_search_provider: str = "tavily"
    web_search_providers: str = "tavily,bing,duckduckgo,searxng"
    web_search_top_k: int = 5
    web_fetch_top_n: int = 3
    web_fetch_concurrency: int = 3
    web_fetch_timeout_seconds: float = 8.0
    web_fetch_total_timeout_seconds: float = 12.0
    web_fetch_min_interval_seconds: float = 1.0
    web_page_max_bytes: int = 2_000_000
    web_content_max_chars: int = 200_000
    web_min_content_chars: int = 300
    web_search_cache_ttl_hours: int = 24
    web_candidate_paragraphs: int = 6
    tavily_api_key: str = ""
    bing_search_api_key: str = ""
    searxng_base_url: str = ""

    # ---- 知识写回（PRD 附录 E.10） ----
    web_writeback_min_relevance: float = 0.45
    web_cache_ttl_days: int = 30
    web_writeback_daily_chunk_cap: int = 500
    web_writeback_max_pages_per_turn: int = 3
    web_writeback_queue_size: int = 50

    @property
    def web_search_provider_chain(self) -> list[str]:
        return [p.strip().lower() for p in self.web_search_providers.split(",") if p.strip()]

    @property
    def mysql_url(self) -> str:
        # 密码可能含特殊字符（如 # @ !），必须 URL 编码，否则 SQLAlchemy 解析错乱
        pwd = quote_plus(self.mysql_password)
        return (
            f"mysql+pymysql://{self.mysql_user}:{pwd}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
