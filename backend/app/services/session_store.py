"""SQLite 会话与日志存储（PRD：会话历史 / 评分日志 / 反馈）。

表：messages（对话历史）、score_logs（评分与检索日志）、feedback（反馈与"记录此问题"占位）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    sources_json TEXT,
    model       TEXT,
    answer_status TEXT,
    request_id  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS retrieval_traces (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    request_id   TEXT,
    question     TEXT,
    complexity   TEXT,
    route        TEXT,
    local_level  INTEGER,
    web_used     INTEGER DEFAULT 0,
    web_urls_json TEXT,
    answer_status TEXT,
    verified     INTEGER DEFAULT 0,
    fallback_level INTEGER,
    kb_written   INTEGER DEFAULT 0,
    elapsed_ms   INTEGER,
    interrupted  INTEGER DEFAULT 0,
    error        TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON retrieval_traces(session_id, id);
CREATE INDEX IF NOT EXISTS idx_traces_request ON retrieval_traces(request_id);

CREATE TABLE IF NOT EXISTS score_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    question    TEXT NOT NULL,
    q_score     INTEGER,
    r_score     INTEGER,
    level       INTEGER,
    confidence  REAL,
    model       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_score_logs_time ON score_logs(created_at);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    question    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL
);
"""


class SessionStore:
    def __init__(self, db_path: str | Path | None = None):
        settings = get_settings()
        self.db_path = Path(db_path or settings.sqlite_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as conn:
            self._migrate_messages(conn)
            conn.executescript(_SCHEMA)

    @staticmethod
    def _migrate_messages(conn: sqlite3.Connection) -> None:
        """旧库增量迁移：messages 补齐 answer_status / request_id 列。"""
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "messages" not in tables:
            return
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        if "answer_status" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN answer_status TEXT")
        if "request_id" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN request_id TEXT")
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # ---- messages ----
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: list[dict] | None = None,
        model: str | None = None,
        answer_status: str | None = None,
        request_id: str | None = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, sources_json, model,"
                " answer_status, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(sources, ensure_ascii=False) if sources else None,
                    model,
                    answer_status,
                    request_id,
                    self._now(),
                ),
            )

    def get_history(
        self, session_id: str, limit: int = 10, verified_only: bool = False
    ) -> list[dict]:
        """返回最近 limit 条消息，按时间正序。

        verified_only=True 时仅回流传已校验/摘录回答（V1.4 会话防污染），
        其余回答仍保留在库中供展示与审计。
        """
        sql = (
            "SELECT role, content, sources_json, model, answer_status, request_id, created_at"
            " FROM messages WHERE session_id = ?"
        )
        if verified_only:
            sql += (
                " AND (role != 'assistant' OR answer_status IN ('verified', 'extractive'))"
            )
        sql += " ORDER BY id DESC LIMIT ?"
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, (session_id, limit)).fetchall()
        rows = list(reversed(rows))
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "sources": json.loads(r["sources_json"]) if r["sources_json"] else [],
                "model": r["model"],
                "answer_status": r["answer_status"],
                "request_id": r["request_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ---- retrieval traces ----
    def add_retrieval_trace(
        self,
        session_id: str,
        *,
        request_id: str | None = None,
        question: str = "",
        complexity: str | None = None,
        route: str | None = None,
        local_level: int | None = None,
        web_used: bool = False,
        web_urls: list[str] | None = None,
        answer_status: str | None = None,
        verified: bool = False,
        fallback_level: int | None = None,
        kb_written: bool = False,
        elapsed_ms: int | None = None,
        interrupted: bool = False,
        error: str | None = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO retrieval_traces (session_id, request_id, question, complexity,"
                " route, local_level, web_used, web_urls_json, answer_status, verified,"
                " fallback_level, kb_written, elapsed_ms, interrupted, error, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    request_id,
                    question,
                    complexity,
                    route,
                    local_level,
                    int(bool(web_used)),
                    json.dumps(web_urls, ensure_ascii=False) if web_urls else None,
                    answer_status,
                    int(bool(verified)),
                    fallback_level,
                    int(bool(kb_written)),
                    elapsed_ms,
                    int(bool(interrupted)),
                    error,
                    self._now(),
                ),
            )

    def get_retrieval_traces(self, session_id: str, limit: int = 20) -> list[dict]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM retrieval_traces WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._trace_dict(r) for r in rows]

    def get_retrieval_trace(self, request_id: str) -> dict | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM retrieval_traces WHERE request_id = ? ORDER BY id DESC LIMIT 1",
                (str(request_id),),
            ).fetchone()
        return self._trace_dict(row) if row else None

    @staticmethod
    def _trace_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "request_id": row["request_id"],
            "question": row["question"],
            "complexity": row["complexity"],
            "route": row["route"],
            "local_level": row["local_level"],
            "web_used": bool(row["web_used"]),
            "web_urls": json.loads(row["web_urls_json"]) if row["web_urls_json"] else [],
            "answer_status": row["answer_status"],
            "verified": bool(row["verified"]),
            "fallback_level": row["fallback_level"],
            "kb_written": bool(row["kb_written"]),
            "elapsed_ms": row["elapsed_ms"],
            "interrupted": bool(row["interrupted"]),
            "error": row["error"],
            "created_at": row["created_at"],
        }

    # ---- score logs ----
    def add_score_log(
        self,
        session_id: str,
        question: str,
        q_score: int | None,
        r_score: int | None,
        level: int | None,
        confidence: float | None,
        model: str | None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO score_logs (session_id, question, q_score, r_score, level,"
                " confidence, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, question, q_score, r_score, level, confidence, model, self._now()),
            )

    def recent_questions(self, days: int = 7, limit: int = 200) -> list[str]:
        # days<=0 表示不启用历史回溯（避免把“当前时刻”误当成一个有效窗口）
        if days <= 0:
            return []
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT question FROM score_logs WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [r["question"] for r in rows]

    # ---- feedback / 记录此问题 ----
    def add_feedback(self, session_id: str, question: str, kind: str, note: str | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO feedback (session_id, question, kind, note, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (session_id, question, kind, note, self._now()),
            )
