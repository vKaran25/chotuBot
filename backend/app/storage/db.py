"""SQLite persistence (stdlib sqlite3). One short-lived connection per operation."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    tool_activity TEXT NOT NULL DEFAULT '[]',
    request_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS usage_records (
    request_id TEXT PRIMARY KEY,
    session_id TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    cost_inr REAL NOT NULL DEFAULT 0,
    cost_source TEXT NOT NULL DEFAULT 'none',
    latency_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    month_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_month ON usage_records(month_key);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def _title_from(text: str) -> str:
    return " ".join(text.split())[:60] or "New conversation"


class Storage:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:  # commit on success, rollback on error
                yield conn
        finally:
            conn.close()

    # ---- sessions -------------------------------------------------------
    def create_session(self, title: str = "New conversation") -> dict[str, Any]:
        sid, ts = new_id(), now_iso()
        with self._conn() as c:
            c.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid, title, ts, ts))
        return {"id": sid, "title": title, "created_at": ts, "updated_at": ts, "message_count": 0}

    def session_exists(self, sid: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is not None

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT s.id, s.title, s.created_at, s.updated_at,
                          (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count
                   FROM sessions s ORDER BY s.updated_at DESC, s.rowid DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, sid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            s = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if s is None:
                return None
            msgs = c.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
        out = dict(s)
        out["messages"] = [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "tool_activity": json.loads(m["tool_activity"]),
                "request_id": m["request_id"],
                "created_at": m["created_at"],
            }
            for m in msgs
        ]
        out["message_count"] = len(msgs)
        return out

    def recent_messages(self, sid: str, limit: int) -> list[dict[str, str]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content FROM (SELECT id, role, content FROM messages WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id",
                (sid, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_exchange(
        self,
        sid: str | None,
        user_text: str,
        assistant_text: str,
        tool_activity: list[dict[str, Any]],
        request_id: str,
    ) -> str:
        """Persist a user+assistant exchange atomically; creates the session when sid is None."""
        ts = now_iso()
        with self._conn() as c:
            if sid is None:
                sid = new_id()
                c.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid, _title_from(user_text), ts, ts))
            else:
                row = c.execute("SELECT title FROM sessions WHERE id=?", (sid,)).fetchone()
                if row is None:
                    raise KeyError(sid)
                if row["title"] == "New conversation":
                    c.execute("UPDATE sessions SET title=? WHERE id=?", (_title_from(user_text), sid))
            c.execute(
                "INSERT INTO messages (session_id, role, content, tool_activity, request_id, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (sid, "user", user_text, "[]", request_id, ts),
            )
            c.execute(
                "INSERT INTO messages (session_id, role, content, tool_activity, request_id, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (sid, "assistant", assistant_text, json.dumps(tool_activity), request_id, ts),
            )
            c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (ts, sid))
        return sid

    # ---- usage ----------------------------------------------------------
    USAGE_COLS = (
        "request_id session_id model input_tokens output_tokens total_tokens cost_usd cost_inr "
        "cost_source latency_ms status error_code tool_call_count month_key created_at"
    ).split()

    def add_usage(self, rec: dict[str, Any]) -> None:
        cols = self.USAGE_COLS
        with self._conn() as c:
            c.execute(
                f"INSERT INTO usage_records ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [rec.get(k) for k in cols],
            )

    def month_spend_inr(self, month_key: str) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(cost_inr),0) AS s FROM usage_records WHERE month_key=?", (month_key,)
            ).fetchone()
        return float(row["s"])

    def list_usage(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM usage_records ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- settings -------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO app_settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
