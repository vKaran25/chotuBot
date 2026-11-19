"""P2-T17: SQLite document metadata (additive rag_* tables in the Phase 1 database file)."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from app.storage import Storage
from app.storage.documents import DocumentStore, WorkspaceNameTaken, DuplicateDocument


def _doc(ws="default", sha="s1", doc_id="d" * 32, name="a.txt"):
    return {"id": doc_id, "workspace_id": ws, "filename": name, "file_type": "txt", "size_bytes": 5,
            "sha256": sha, "chunk_count": 2, "page_count": None, "embedder": "hash-v1"}


def _chunks(doc_id="d" * 32, ws="default", n=2):
    return [{"id": f"{doc_id[:30]}{i:02d}", "document_id": doc_id, "workspace_id": ws, "ordinal": i,
             "text": f"chunk {i}", "location": {"line_start": i + 1, "line_end": i + 1},
             "start_offset": i * 10, "end_offset": i * 10 + 7, "vector": np.full(4, i + 1, np.float32)}
            for i in range(n)]


@pytest.fixture
def stores(tmp_db):
    s = Storage(tmp_db)
    return s, DocumentStore(tmp_db)


def test_default_workspace_exists_and_tables_are_additive(stores, tmp_db):
    _, d = stores
    [ws] = d.list_workspaces()
    assert ws["id"] == "default" and ws["name"] == "Default" and ws["document_count"] == 0
    assert ws["sharing"] == {"allowed": False, "updated_at": None}
    with sqlite3.connect(tmp_db) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sessions", "messages", "usage_records", "app_settings"} <= tables
    assert {"rag_workspaces", "rag_documents", "rag_chunks", "rag_citations"} <= tables


def test_phase1_records_untouched(stores, tmp_db):
    s, d = stores
    sid = s.save_exchange(None, "hi", "hello", [], "r1")
    d.insert_document(_doc(), _chunks())
    DocumentStore(tmp_db)  # re-opening must not alter anything
    assert s.get_session(sid)["message_count"] == 2


def test_workspaces_create_unique_names_and_consent(stores):
    _, d = stores
    w = d.create_workspace("Research")
    assert len(w["id"]) == 32 and w["name"] == "Research"
    with pytest.raises(WorkspaceNameTaken):
        d.create_workspace("research")
    upd = d.set_consent(w["id"], True)
    assert upd["sharing"]["allowed"] is True and upd["sharing"]["updated_at"]
    assert d.get_workspace("default")["sharing"]["allowed"] is False
    assert d.set_consent("f" * 32, True) is None
    assert [x["id"] for x in d.list_workspaces()] == ["default", w["id"]]


def test_document_roundtrip_survives_new_connection(stores, tmp_db):
    _, d = stores
    d.insert_document(_doc(), _chunks())
    d2 = DocumentStore(tmp_db)
    doc = d2.get_document("d" * 32)
    assert doc["filename"] == "a.txt" and doc["status"] == "indexed" and doc["index_version"] == "v1"
    assert doc["chunk_count"] == 2 and doc["created_at"] and doc["updated_at"]
    assert "sha256" not in d2.public(doc) and "embedder" not in d2.public(doc)
    ids, vecs = d2.workspace_vectors("default", "hash-v1", 4)
    assert ids == [c["id"] for c in _chunks()] and vecs.shape == (2, 4) and vecs[1][0] == 2.0
    assert d2.workspace_vectors("default", "other", 4)[0] == []
    rows = d2.get_chunks(ids)
    assert rows[ids[0]]["text"] == "chunk 0" and rows[ids[0]]["location"] == {"line_start": 1, "line_end": 1}
    assert d2.list_workspaces()[0]["document_count"] == 1


def test_duplicate_bytes_rejected_per_workspace(stores):
    _, d = stores
    d.insert_document(_doc(), _chunks())
    with pytest.raises(DuplicateDocument) as e:
        d.insert_document(_doc(doc_id="e" * 32), _chunks("e" * 32))
    assert e.value.document_id == "d" * 32
    ws = d.create_workspace("Other")
    d.insert_document(_doc(ws=ws["id"], doc_id="e" * 32), _chunks("e" * 32, ws["id"]))


def test_replace_chunks_and_delete(stores):
    s, d = stores
    d.insert_document(_doc(), _chunks())
    d.replace_chunks("d" * 32, _chunks(n=3), page_count=None, embedder="hash-v1")
    assert d.get_document("d" * 32)["chunk_count"] == 3
    sid = s.save_exchange(None, "q", "a [1]", [], "req1")
    [cit] = d.add_citations(sid, "req1", "default",
                            [{"n": 1, "chunk_id": _chunks()[0]["id"], "document_id": "d" * 32,
                              "filename": "a.txt", "location": {"line_start": 1, "line_end": 1}}])
    assert d.get_citation(cit["id"])["status"] == "ok"
    assert d.delete_document("d" * 32) is True
    assert d.get_document("d" * 32) is None
    assert d.get_chunks([c["id"] for c in _chunks(n=3)]) == {}
    gone = d.get_citation(cit["id"])
    assert gone["status"] == "source_deleted" and gone["filename"] is None and gone["document_id"] is None
    assert d.delete_document("d" * 32) is False


def test_citations_follow_session_deletion(stores):
    s, d = stores
    d.insert_document(_doc(), _chunks())
    sid = s.save_exchange(None, "q", "a", [], "req1")
    [cit] = d.add_citations(sid, "req1", "default", [{"n": 1, "chunk_id": _chunks()[0]["id"],
                                                      "document_id": "d" * 32, "filename": "a.txt",
                                                      "location": {}}])
    assert list(d.session_citations(sid)) == ["req1"]
    assert s.delete_session(sid)  # Phase 1 code path, unchanged
    assert d.get_citation(cit["id"]) is None
