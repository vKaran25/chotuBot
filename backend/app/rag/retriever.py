"""Scoped retrieval behind one small interface (P2-REQ-6, P2-REQ-9).

Phase 3 extension point: add other `Retriever` implementations (BM25, hybrid, reranking) and choose one in
`DocumentService`; chat wiring only depends on this protocol and `Hit`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.rag.embeddings import Embedder
from app.rag.index import IndexManager
from app.storage.documents import DocumentStore


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    document_id: str
    filename: str
    text: str
    location: dict[str, Any]
    score: float


class Retriever(Protocol):
    min_score: float

    def retrieve(self, workspace_id: str, query: str, k: int) -> list[Hit]:
        """Best-first hits from `workspace_id` only. May include weak hits; callers apply `min_score`."""
        ...


class FaissRetriever:
    def __init__(self, store: DocumentStore, indexes: IndexManager, embedder: Embedder,
                 rebuild: Callable[[str], None]):
        self.store, self.indexes, self.embedder, self._rebuild = store, indexes, embedder, rebuild
        self.min_score = embedder.min_score

    def retrieve(self, workspace_id: str, query: str, k: int) -> list[Hit]:
        q = self.embedder.embed_query(query)
        found = self.indexes.search(workspace_id, q, k)
        if found is None:  # no cache file yet (or stale): rebuild from SQLite once
            self._rebuild(workspace_id)
            found = self.indexes.search(workspace_id, q, k) or []
        rows = self.store.get_chunks([cid for cid, _ in found])
        hits = []
        for cid, score in found:
            row = rows.get(cid)
            if row is None or row["workspace_id"] != workspace_id:  # deleted meanwhile, or never this workspace's
                continue
            hits.append(Hit(cid, row["document_id"], row["filename"], row["text"], row["location"], score))
        return hits
