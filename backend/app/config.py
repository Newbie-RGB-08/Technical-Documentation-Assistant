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
