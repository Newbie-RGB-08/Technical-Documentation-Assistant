"""线上检索结果与网页正文缓存（PRD E.9.4）。

SQLite 实现：搜索缓存键为归一化查询 + provider + top_k（TTL 24 小时）；
抓取内容缓存键为 URL（TTL 与网页知识写回一致，默认 30 天）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.services.web_search import normalize_query

_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_search_cache (
    cache_key   TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    provider    TEXT NOT NULL,
    top_k       INTEGER NOT NULL,
    results_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_search_created ON web_search_cache(created_at);

CREATE TABLE IF NOT EXISTS web_fetch_cache (
    url          TEXT PRIMARY KEY,
    title        TEXT,
    markdown     TEXT NOT NULL,
    provider     TEXT,
    retrieved_at TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_fetch_created ON web_fetch_cache(created_at);
"""


def _now() -> datetime:
    return datetime.now()


class WebCacheStore:
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        search_ttl_hours: float | None = None,
        page_ttl_days: float | None = None,
        clock=None,
    ):
        settings = get_settings()
        self.db_path = Path(db_path or (settings.data_dir / "web_cache.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.search_ttl = timedelta(
            hours=float(settings.web_search_cache_ttl_hours if search_ttl_hours is None else search_ttl_hours)
        )
        self.page_ttl = timedelta(
            days=float(settings.web_cache_ttl_days if page_ttl_days is None else page_ttl_days)
        )
        self._clock = clock or _now
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _fresh(self, created_at: str, ttl: timedelta) -> bool:
        try:
            created = datetime.fromisoformat(created_at)
        except (TypeError, ValueError):
            return False
        return self._clock() - created <= ttl

    # ---- 搜索缓存 ----
    @staticmethod
    def _key(query: str, provider: str, top_k: int) -> str:
        return f"{normalize_query(query)}|{provider}|{int(top_k)}"

    def get_search(self, query: str, provider: str, top_k: int) -> list[dict] | None:
        key = self._key(query, provider, top_k)
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT results_json, created_at FROM web_search_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if not self._fresh(row["created_at"], self.search_ttl):
                conn.execute("DELETE FROM web_search_cache WHERE cache_key = ?", (key,))
                conn.commit()
                return None
            try:
                data = json.loads(row["results_json"])
            except (TypeError, ValueError):
                return None
            return data if isinstance(data, list) else None

    def set_search(
        self, query: str, provider: str, top_k: int, results: list[dict]
    ) -> None:
        key = self._key(query, provider, top_k)
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO web_search_cache "
                "(cache_key, query, provider, top_k, results_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    normalize_query(query),
                    provider,
                    int(top_k),
                    json.dumps(results, ensure_ascii=False),
                    self._clock().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()

    # ---- 抓取内容缓存 ----
    def get_page(self, url: str) -> dict | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT url, title, markdown, provider, retrieved_at, created_at "
                "FROM web_fetch_cache WHERE url = ?",
                (str(url),),
            ).fetchone()
            if row is None:
                return None
            if not self._fresh(row["created_at"], self.page_ttl):
                conn.execute("DELETE FROM web_fetch_cache WHERE url = ?", (str(url),))
                conn.commit()
                return None
            return {
                "url": row["url"],
                "title": row["title"] or "",
                "markdown": row["markdown"],
                "provider": row["provider"] or "",
                "retrieved_at": row["retrieved_at"] or "",
            }

    def set_page(self, page: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO web_fetch_cache "
                "(url, title, markdown, provider, retrieved_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(page.get("url") or ""),
                    str(page.get("title") or ""),
                    str(page.get("markdown") or ""),
                    str(page.get("provider") or ""),
                    str(page.get("retrieved_at") or ""),
                    self._clock().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()

    def prune(self, days: float = 90) -> dict:
        """清理超过指定天数的缓存行，返回删除数量。"""
        cutoff = (self._clock() - timedelta(days=float(days))).isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            search_deleted = conn.execute(
                "DELETE FROM web_search_cache WHERE created_at < ?", (cutoff,)
            ).rowcount
            page_deleted = conn.execute(
                "DELETE FROM web_fetch_cache WHERE created_at < ?", (cutoff,)
            ).rowcount
            conn.commit()
        return {"search": int(search_deleted), "pages": int(page_deleted)}
