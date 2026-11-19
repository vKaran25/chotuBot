"""One exact FAISS index per workspace (P2-REQ-6). SQLite holds the vectors; these files are a rebuildable cache."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import faiss
import numpy as np

from app.rag.config import INDEX_VERSION

log = logging.getLogger(__name__)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class IndexManager:
    """`IndexFlatIP` over L2-normalised vectors = exact cosine search. Thread-safe."""

    def __init__(self, root: Path, dim: int, embedder_name: str):
        self.root, self.dim, self.embedder_name = Path(root), dim, embedder_name
        self._cache: dict[str, tuple[faiss.Index, list[str]]] = {}
        self._lock = threading.RLock()

    def _dir(self, ws: str) -> Path:
        return self.root / ws

    def rebuild(self, ws: str, ids: list[str], vectors: np.ndarray) -> None:
        vectors = np.ascontiguousarray(vectors, dtype=np.float32).reshape(-1, self.dim)
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors differ in length")
        index = faiss.IndexFlatIP(self.dim)
        if len(ids):
            index.add(vectors)
        with self._lock:
            d = self._dir(ws)
            d.mkdir(parents=True, exist_ok=True)
            # serialize_index + Python I/O instead of faiss.write_index: works with non-ASCII Windows paths.
            _atomic_write(d / "index.faiss", faiss.serialize_index(index).tobytes())
            meta = {"index_version": INDEX_VERSION, "embedder": self.embedder_name, "dim": self.dim, "ids": ids}
            _atomic_write(d / "ids.json", json.dumps(meta).encode())
            self._cache[ws] = (index, list(ids))

    def _load(self, ws: str) -> tuple[faiss.Index, list[str]] | None:
        d = self._dir(ws)
        try:
            meta = json.loads((d / "ids.json").read_text(encoding="utf-8"))
            if (meta.get("index_version"), meta.get("embedder"), meta.get("dim")) != (
                INDEX_VERSION, self.embedder_name, self.dim
            ):
                return None
            index = faiss.deserialize_index(np.frombuffer((d / "index.faiss").read_bytes(), dtype=np.uint8))
            if index.ntotal != len(meta["ids"]) or index.d != self.dim:
                return None
            return index, list(meta["ids"])
        except Exception:  # missing or corrupt cache files -> caller rebuilds from SQLite
            return None

    def search(self, ws: str, query: np.ndarray, k: int) -> list[tuple[str, float]] | None:
        """Top-k (chunk_id, cosine) for one workspace, or None when no usable index exists yet."""
        with self._lock:
            entry = self._cache.get(ws)
            if entry is None:
                entry = self._load(ws)
                if entry is None:
                    return None
                self._cache[ws] = entry
        index, ids = entry
        if not ids or k <= 0:
            return []
        q = np.ascontiguousarray(query, dtype=np.float32).reshape(1, self.dim)
        scores, rows = index.search(q, min(k, len(ids)))
        return [(ids[r], float(s)) for s, r in zip(scores[0], rows[0]) if r >= 0]

    def drop(self, ws: str) -> None:
        with self._lock:
            self._cache.pop(ws, None)
