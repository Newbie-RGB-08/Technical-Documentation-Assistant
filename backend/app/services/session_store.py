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
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

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
            conn.executescript(_SCHEMA)

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
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, sources_json, model, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(sources, ensure_ascii=False) if sources else None,
                    model,
                    self._now(),
                ),
            )

    def get_history(self, session_id: str, limit: int = 10) -> list[dict]:
        """返回最近 limit 条消息，按时间正序。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, sources_json, model, created_at FROM messages"
                " WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        rows = list(reversed(rows))
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "sources": json.loads(r["sources_json"]) if r["sources_json"] else [],
                "model": r["model"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

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
