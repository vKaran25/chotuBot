"""Phase 2 document metadata in SQLite (P2-REQ-7). Additive `rag_*` tables in the same database file.

Phase 1 tables are never altered. Citation rows reference `sessions(id) ON DELETE CASCADE`, so the unchanged
Phase 1 conversation delete also removes them.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from app.rag.config import INDEX_VERSION
from app.storage.db import new_id, now_iso

DEFAULT_WORKSPACE = "default"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    sharing_allowed INTEGER NOT NULL DEFAULT 0,
    sharing_updated_at TEXT
);
CREATE TABLE IF NOT EXISTS rag_documents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES rag_workspaces(id),
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK (file_type IN ('pdf','md','txt')),
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'indexed',
    chunk_count INTEGER NOT NULL,
    page_count INTEGER,
    index_version TEXT NOT NULL,
    embedder TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, sha256)
);
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    location TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    index_version TEXT NOT NULL,
    embedder TEXT NOT NULL,
    vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_ws ON rag_chunks(workspace_id, embedder, document_id, ordinal);
CREATE TABLE IF NOT EXISTS rag_citations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    chunk_id TEXT,
    document_id TEXT,
    filename TEXT,
    location TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rag_citations_session ON rag_citations(session_id, request_id, n);
CREATE INDEX IF NOT EXISTS idx_rag_citations_doc ON rag_citations(document_id);
"""

PUBLIC_DOC_FIELDS = ("id", "workspace_id", "filename", "file_type", "size_bytes", "status", "chunk_count",
                     "page_count", "index_version", "created_at", "updated_at")


class WorkspaceNameTaken(Exception):
    pass


class DuplicateDocument(Exception):
    def __init__(self, document_id: str):
        super().__init__(document_id)
        self.document_id = document_id


def _ts() -> str:
    return now_iso(timespec="microseconds")


class DocumentStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            c.execute(
                "INSERT OR IGNORE INTO rag_workspaces (id, name, name_key, created_at) VALUES (?,?,?,?)",
                (DEFAULT_WORKSPACE, "Default", "default", _ts()),
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")  # deleted document text is overwritten, not left in free pages
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---- workspaces -----------------------------------------------------
    @staticmethod
    def _ws(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "document_count": row["document_count"],
            "sharing": {"allowed": bool(row["sharing_allowed"]), "updated_at": row["sharing_updated_at"]},
        }

    _WS_SELECT = (
        "SELECT w.*, (SELECT COUNT(*) FROM rag_documents d WHERE d.workspace_id = w.id) AS document_count "
        "FROM rag_workspaces w"
    )

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                f"{self._WS_SELECT} ORDER BY (w.id = 'default') DESC, w.created_at, w.rowid"
            ).fetchall()
        return [self._ws(r) for r in rows]

    def get_workspace(self, ws_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(f"{self._WS_SELECT} WHERE w.id = ?", (ws_id,)).fetchone()
        return self._ws(row) if row else None

    def create_workspace(self, name: str) -> dict[str, Any]:
        ws_id = new_id()
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO rag_workspaces (id, name, name_key, created_at) VALUES (?,?,?,?)",
                    (ws_id, name, name.casefold(), _ts()),
                )
        except sqlite3.IntegrityError:
            raise WorkspaceNameTaken(name) from None
        return self.get_workspace(ws_id)

    def set_consent(self, ws_id: str, allowed: bool) -> dict[str, Any] | None:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE rag_workspaces SET sharing_allowed=?, sharing_updated_at=? WHERE id=?",
                (1 if allowed else 0, _ts(), ws_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_workspace(ws_id)

    # ---- documents ------------------------------------------------------
    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {k: doc[k] for k in PUBLIC_DOC_FIELDS}

    @staticmethod
    def _chunk_rows(chunks: list[dict[str, Any]], embedder: str) -> list[tuple]:
        return [
            (c["id"], c["document_id"], c["workspace_id"], c["ordinal"], c["text"], json.dumps(c["location"]),
             c["start_offset"], c["end_offset"], INDEX_VERSION, embedder,
             np.asarray(c["vector"], dtype=np.float32).tobytes())
            for c in chunks
        ]

    _CHUNK_INSERT = (
        "INSERT INTO rag_chunks (id, document_id, workspace_id, ordinal, text, location, start_offset, "
        "end_offset, index_version, embedder, vector) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    )

    def insert_document(self, doc: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
        ts = _ts()
        try:
            with self._conn() as c:
                c.execute("BEGIN IMMEDIATE")
                dup = c.execute(
                    "SELECT id FROM rag_documents WHERE workspace_id=? AND sha256=?", (doc["workspace_id"], doc["sha256"])
                ).fetchone()
                if dup:
                    raise DuplicateDocument(dup["id"])
                c.execute(
                    "INSERT INTO rag_documents (id, workspace_id, filename, file_type, size_bytes, sha256, status, "
                    "chunk_count, page_count, index_version, embedder, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,'indexed',?,?,?,?,?,?)",
                    (doc["id"], doc["workspace_id"], doc["filename"], doc["file_type"], doc["size_bytes"],
                     doc["sha256"], len(chunks), doc.get("page_count"), INDEX_VERSION, doc["embedder"], ts, ts),
                )
                c.executemany(self._CHUNK_INSERT, self._chunk_rows(chunks, doc["embedder"]))
        except sqlite3.IntegrityError:
            dup_id = self._find_dup(doc["workspace_id"], doc["sha256"])
            if dup_id:
                raise DuplicateDocument(dup_id) from None
            raise
        return self.get_document(doc["id"])

    def _find_dup(self, ws: str, sha: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT id FROM rag_documents WHERE workspace_id=? AND sha256=?", (ws, sha)).fetchone()
        return row["id"] if row else None

    def find_duplicate(self, ws: str, sha: str) -> str | None:
        return self._find_dup(ws, sha)

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM rag_documents WHERE id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self, ws: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM rag_documents WHERE workspace_id=? ORDER BY created_at DESC, rowid DESC", (ws,)
            ).fetchall()
        return [dict(r) for r in rows]

    def replace_chunks(self, doc_id: str, chunks: list[dict[str, Any]], *, page_count: int | None, embedder: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM rag_chunks WHERE document_id=?", (doc_id,))
            c.executemany(self._CHUNK_INSERT, self._chunk_rows(chunks, embedder))
            c.execute(
                "UPDATE rag_documents SET chunk_count=?, page_count=?, index_version=?, embedder=?, updated_at=? "
                "WHERE id=?",
                (len(chunks), page_count, INDEX_VERSION, embedder, _ts(), doc_id),
            )

    def delete_document(self, doc_id: str) -> bool:
        """Removes the document row and all its chunks/vectors; clears what old citations pointed at."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM rag_documents WHERE id=?", (doc_id,)).fetchone() is None:
                return False
            c.execute(
                "UPDATE rag_citations SET chunk_id=NULL, document_id=NULL, filename=NULL, location=NULL "
                "WHERE document_id=?",
                (doc_id,),
            )
            c.execute("DELETE FROM rag_chunks WHERE document_id=?", (doc_id,))
            c.execute("DELETE FROM rag_documents WHERE id=?", (doc_id,))
        return True

    # ---- vectors / chunks -----------------------------------------------
    def workspace_vectors(self, ws: str, embedder: str, dim: int) -> tuple[list[str], np.ndarray]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, vector FROM rag_chunks WHERE workspace_id=? AND embedder=? AND index_version=? "
                "ORDER BY document_id, ordinal",
                (ws, embedder, INDEX_VERSION),
            ).fetchall()
        if not rows:
            return [], np.zeros((0, dim), dtype=np.float32)
        return [r["id"] for r in rows], np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])

    def opening_chunks(self, ws: str, embedder: str, limit: int) -> list[dict[str, Any]]:
        """The first chunks of each document, taken in turn (newest document first): chunk 0 of every document,
        then chunk 1, ... Used when no chunk matches a question closely (owner decision 2026-11-19)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT ch.id, ch.document_id, ch.workspace_id, ch.text, ch.location, d.filename "
                "FROM rag_chunks ch JOIN rag_documents d ON d.id = ch.document_id "
                "WHERE ch.workspace_id=? AND ch.embedder=? AND ch.index_version=? "
                "ORDER BY ch.ordinal, d.created_at DESC, d.rowid DESC LIMIT ?",
                (ws, embedder, INDEX_VERSION, limit),
            ).fetchall()
        return [{**dict(r), "location": json.loads(r["location"])} for r in rows]

    def get_chunks(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT ch.id, ch.document_id, ch.workspace_id, ch.text, ch.location, d.filename "
                f"FROM rag_chunks ch JOIN rag_documents d ON d.id = ch.document_id WHERE ch.id IN ({q})",
                ids,
            ).fetchall()
        return {r["id"]: {**dict(r), "location": json.loads(r["location"])} for r in rows}

    # ---- citations ------------------------------------------------------
    @staticmethod
    def _cit(row: sqlite3.Row) -> dict[str, Any]:
        ok = row["document_id"] is not None
        return {
            "id": row["id"],
            "n": row["n"],
            "workspace_id": row["workspace_id"],
            "document_id": row["document_id"],
            "filename": row["filename"],
            "location": json.loads(row["location"]) if row["location"] else None,
            "status": "ok" if ok else "source_deleted",
        }

    def add_citations(self, session_id: str, request_id: str, ws: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ts = _ts()
        rows = [(new_id(), session_id, request_id, ws, it["n"], it["chunk_id"], it["document_id"], it["filename"],
                 json.dumps(it["location"]), ts) for it in items]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO rag_citations (id, session_id, request_id, workspace_id, n, chunk_id, document_id, "
                "filename, location, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            out = c.execute(
                f"SELECT * FROM rag_citations WHERE id IN ({','.join('?' * len(rows))}) ORDER BY n", [r[0] for r in rows]
            ).fetchall() if rows else []
        return [self._cit(r) for r in out]

    def get_citation(self, cit_id: str) -> dict[str, Any] | None:
        """Citation with its excerpt text; text is read from a chunk of the citation's own workspace only."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM rag_citations WHERE id=?", (cit_id,)).fetchone()
            if row is None:
                return None
            chunk = None
            if row["chunk_id"]:
                chunk = c.execute(
                    "SELECT text FROM rag_chunks WHERE id=? AND workspace_id=?", (row["chunk_id"], row["workspace_id"])
                ).fetchone()
        out = {**self._cit(row), "request_id": row["request_id"], "session_id": row["session_id"]}
        if chunk is None:  # document deleted (or chunk gone after a content-changing reindex)
            out.update(status="source_deleted", document_id=None, filename=None, location=None, text=None)
        else:
            out["text"] = chunk["text"]
        return out

    def session_citations(self, session_id: str) -> dict[str, list[dict[str, Any]]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM rag_citations WHERE session_id=? ORDER BY created_at, request_id, n", (session_id,)
            ).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["request_id"], []).append(self._cit(r))
        return out
