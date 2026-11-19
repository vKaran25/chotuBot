"""P2-T15: local embedding and FAISS baseline contract (hash embedder; the real model is opt-in, see below)."""
from __future__ import annotations

import os

import numpy as np
import pytest

from app.rag.embeddings import HashEmbedder, build_embedder
from app.rag.index import IndexManager


def test_hash_embedder_is_deterministic_normalised_384d():
    e = HashEmbedder()
    a = e.embed_documents(["The rover battery lasts 14 hours.", "Budget memo"])
    b = e.embed_documents(["The rover battery lasts 14 hours.", "Budget memo"])
    assert a.shape == (2, 384) and a.dtype == np.float32
    assert np.array_equal(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0, atol=1e-5)


def test_hash_embedder_similarity_follows_shared_words():
    e = HashEmbedder()
    docs = e.embed_documents(["rover battery lasts fourteen hours", "budget approval director rupees"])
    q = e.embed_query("how long does the rover battery last")
    scores = docs @ q
    assert scores[0] > scores[1] and scores[0] >= e.min_score
    assert float(e.embed_query("zebra xylophone") @ docs[0]) < e.min_score


def test_empty_query_gives_zero_vector_not_nan():
    v = HashEmbedder().embed_query("the a of")
    assert not np.isnan(v).any() and float(np.linalg.norm(v)) == 0.0


def _vecs(n: int, dim: int = 384, seed: int = 0) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal((n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_index_rebuild_search_and_persist(tmp_path):
    vecs = _vecs(10)
    ids = [f"c{i}" for i in range(10)]
    m = IndexManager(tmp_path, 384, "hash-v1")
    m.rebuild("default", ids, vecs)
    hits = m.search("default", vecs[3], 5)
    assert hits[0][0] == "c3" and hits[0][1] == pytest.approx(1.0, abs=1e-5)
    assert len(hits) == 5 and [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)
    # A fresh manager (app restart) loads the same index from disk.
    m2 = IndexManager(tmp_path, 384, "hash-v1")
    assert m2.search("default", vecs[3], 1) == [hits[0]]


def test_index_is_per_workspace(tmp_path):
    m = IndexManager(tmp_path, 384, "hash-v1")
    m.rebuild("a", ["a1"], _vecs(1, seed=1))
    m.rebuild("b", ["b1"], _vecs(1, seed=2))
    assert [i for i, _ in m.search("a", _vecs(1, seed=2)[0], 5)] == ["a1"]
    assert [i for i, _ in m.search("b", _vecs(1, seed=1)[0], 5)] == ["b1"]


def test_missing_or_empty_index_returns_none_or_empty(tmp_path):
    m = IndexManager(tmp_path, 384, "hash-v1")
    assert m.search("never-built", _vecs(1)[0], 5) is None
    m.rebuild("empty", [], np.zeros((0, 384), np.float32))
    assert m.search("empty", _vecs(1)[0], 5) == []


def test_index_from_other_embedder_is_treated_as_missing(tmp_path):
    IndexManager(tmp_path, 384, "hash-v1").rebuild("w", ["x"], _vecs(1))
    assert IndexManager(tmp_path, 384, "other-model").search("w", _vecs(1)[0], 5) is None


def test_corrupt_index_file_is_treated_as_missing(tmp_path):
    m = IndexManager(tmp_path, 384, "hash-v1")
    m.rebuild("w", ["x"], _vecs(1))
    (tmp_path / "w" / "index.faiss").write_bytes(b"garbage")
    assert IndexManager(tmp_path, 384, "hash-v1").search("w", _vecs(1)[0], 5) is None


def test_build_embedder_selects_hash_without_loading_a_model(tmp_path):
    e = build_embedder("hash", tmp_path)
    assert isinstance(e, HashEmbedder) and e.dim == 384


@pytest.mark.skipif(os.environ.get("CHOTU_TEST_REAL_EMBEDDER") != "1",
                    reason="opt-in: set CHOTU_TEST_REAL_EMBEDDER=1 (uses the cached bge-small model in data/models)")
def test_real_fastembed_model_ranks_relevant_text_first():
    from app.config import REPO_ROOT
    e = build_embedder("fastembed", REPO_ROOT / "data" / "models")
    docs = e.embed_documents(["The Orion rover battery lasts 14 hours on a full charge.",
                              "The field programme budget for this year is 42 lakh rupees."])
    assert docs.shape == (2, 384)
    q = e.embed_query("How long does the battery last?")
    s = docs @ q
    assert s[0] > s[1] and s[0] >= e.min_score
