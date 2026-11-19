"""Local embedding models (P2-REQ-5). Vectors are float32 and L2-normalised, so inner product = cosine."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

from app.rag.config import EMBED_DIM, EMBED_MODEL, FASTEMBED_MIN_SCORE, HASH_MIN_SCORE
from app.rag.errors import DocumentError

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int
    min_score: float  # placeholder "enough evidence" cutoff for this embedder (calibrated in Phase 3)

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    if m.ndim == 1:
        n = float(np.linalg.norm(m))
        return m / n if n > 0 else m
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return np.divide(m, n, out=np.zeros_like(m), where=n > 0)


def embedding_unavailable() -> DocumentError:
    return DocumentError(503, "embedding_unavailable", "The local embedding model could not be loaded.")


STOPWORDS = frozenset(
    "the a an and or of to in on for with is are was were be by at it its this that as from how what when where "
    "which who whom why does do did can could should would will my me i you your our we they their there".split()
)


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. **Tests and offline QA only**: no semantics, no download."""

    name = "hash-v1"
    dim = EMBED_DIM
    min_score = HASH_MIN_SCORE

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in re.findall(r"[a-z0-9]+", text.casefold()):
            if len(tok) < 2 or tok in STOPWORDS:
                continue
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        return _normalise(v)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


class FastEmbedEmbedder:
    """BAAI/bge-small-en-v1.5 through fastembed (ONNX on CPU). Loaded lazily on first use; cached on disk."""

    name = EMBED_MODEL
    dim = EMBED_DIM
    min_score = FASTEMBED_MIN_SCORE

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._model = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._model is None:
                os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
                try:
                    from fastembed import TextEmbedding

                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    self._model = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(self.cache_dir))
                except Exception as e:  # download failure, missing package, corrupt cache ...
                    log.error("embedding model unavailable: %s", type(e).__name__)
                    raise embedding_unavailable() from None
            return self._model

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        model = self._get()
        try:
            return _normalise(np.stack(list(model.embed(texts, batch_size=32))))
        except Exception:
            raise embedding_unavailable() from None

    def embed_query(self, text: str) -> np.ndarray:
        model = self._get()
        try:
            return _normalise(np.asarray(next(iter(model.query_embed(text)))))
        except Exception:
            raise embedding_unavailable() from None


def build_embedder(kind: str, cache_dir: Path) -> Embedder:
    return HashEmbedder() if kind == "hash" else FastEmbedEmbedder(cache_dir)
