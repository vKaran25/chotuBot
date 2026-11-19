"""P2-T19/T21/T23/T25: document management, workspace isolation, consent, cited answers, Phase 1 compatibility.

All offline: scripted/demo models and the deterministic hash embedder. No network, no model download.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import Field

from app.config import REPO_ROOT, Settings
from app.main import create_app
from app.models.fakes import ScriptedChatModel
from app.rag.answer import DEMO_PREFIX, NO_EVIDENCE_REPLY
from app.rag.config import RagSettings
from tests.conftest import TEST_KEY
from tests.helpers import call, text

FX = REPO_ROOT / "evals" / "fixtures" / "phase2"
PDF, MD, TXT = "orion-handbook.pdf", "orion-field-notes.md", "kestrel-budget.txt"


class RecordingModel(ScriptedChatModel):
    seen: list[Any] = Field(default_factory=list, repr=False)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


class Factory:
    def __init__(self, *scripts):
        self.scripts, self.models = list(scripts), []

    @property
    def built(self) -> int:
        return len(self.models)

    def __call__(self, settings, model_id):
        m = RecordingModel(script=self.scripts.pop(0) if self.scripts else [])
        self.models.append(m)
        return m


def client_for(tmp_db, provider="nim", *scripts):
    settings = Settings(_env_file=None, NVIDIA_API_KEY=TEST_KEY, CHOTU_PROVIDER=provider, CHOTU_DB_PATH=tmp_db)
    factory = Factory(*scripts)
    app = create_app(settings, model_factory=factory, callbacks=[],
                     rag_settings=RagSettings(_env_file=None, CHOTU_EMBEDDER="hash"))
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False), factory


@pytest.fixture
def nim(tmp_db):
    return client_for(tmp_db, "nim")


@pytest.fixture
def demo(tmp_db):
    return client_for(tmp_db, "demo")


def upload(c, name, data=None, ws="default", ctype="application/octet-stream"):
    data = (FX / name).read_bytes() if data is None else data
    return c.post("/api/documents", files={"file": (name, data, ctype)}, data={"workspace_id": ws})


def err(r):
    return r.json()["error"]


def state(c, tmp_db):
    with sqlite3.connect(tmp_db) as con:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("sessions", "messages", "usage_records", "rag_documents", "rag_chunks", "rag_citations")}


# ---- workspaces ----------------------------------------------------------
def test_workspaces_and_consent(nim):
    c, _ = nim
    r = c.get("/api/workspaces").json()
    assert r["consent_required"] is True
    assert r["workspaces"] == [{"id": "default", "name": "Default", "created_at": r["workspaces"][0]["created_at"],
                                "document_count": 0, "sharing": {"allowed": False, "updated_at": None}}]
    w = c.post("/api/workspaces", json={"name": "  Research "})
    assert w.status_code == 201 and w.json()["name"] == "Research"
    assert err(c.post("/api/workspaces", json={"name": "RESEARCH"}))["code"] == "workspace_name_taken"
    for body in ({"name": ""}, {"name": "   "}, {"name": "x" * 61}, {"name": "a", "extra": 1}, {}):
        assert c.post("/api/workspaces", json=body).status_code == 422
    on = c.put("/api/workspaces/default/consent", json={"allowed": True}).json()
    assert on["sharing"]["allowed"] is True and on["sharing"]["updated_at"]
    for body in ({"allowed": "yes"}, {"allowed": 1}, {}):
        assert c.put("/api/workspaces/default/consent", json=body).status_code == 422
    off = c.put("/api/workspaces/default/consent", json={"allowed": False}).json()
    assert off["sharing"]["allowed"] is False and off["sharing"]["updated_at"] >= on["sharing"]["updated_at"]
    assert c.get("/api/workspaces").json()["workspaces"][1]["sharing"]["allowed"] is False
    for bad in ("f" * 32, "Not-An-Id"):
        r = c.put(f"/api/workspaces/{bad}/consent", json={"allowed": True})
        assert r.status_code == 404 and err(r)["code"] == "workspace_not_found"


def test_demo_provider_needs_no_consent(demo):
    c, _ = demo
    assert c.get("/api/workspaces").json()["consent_required"] is False


# ---- upload / list / reindex / delete -----------------------------------
def test_upload_three_formats_list_reindex_delete(nim, tmp_db):
    c, _ = nim
    docs = {}
    for name, ftype in ((TXT, "txt"), (MD, "md"), (PDF, "pdf")):
        r = upload(c, name)
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["file_type"] == ftype and d["filename"] == name and d["status"] == "indexed"
        assert d["size_bytes"] == len((FX / name).read_bytes()) and d["chunk_count"] >= 1
        assert d["index_version"] == "v1" and d["page_count"] == (2 if ftype == "pdf" else None)
        docs[ftype] = d
    listed = c.get("/api/documents").json()["documents"]
    assert [d["file_type"] for d in listed] == ["pdf", "md", "txt"]
    assert c.get("/api/workspaces").json()["workspaces"][0]["document_count"] == 3
    assert (tmp_db.parent / f"{tmp_db.stem}.rag" / "indexes" / "default" / "index.faiss").is_file()

    r = c.post(f"/api/documents/{docs['md']['id']}/reindex")
    assert r.status_code == 200 and r.json()["chunk_count"] == docs["md"]["chunk_count"]
    assert r.json()["updated_at"] > docs["md"]["updated_at"]

    stored = tmp_db.parent / f"{tmp_db.stem}.rag" / "files" / "default" / f"{docs['txt']['id']}.txt"
    assert stored.is_file()
    assert c.delete(f"/api/documents/{docs['txt']['id']}").status_code == 204
    assert not stored.exists()
    assert [d["id"] for d in c.get("/api/documents").json()["documents"]] == [docs["pdf"]["id"], docs["md"]["id"]]
    for r in (c.delete(f"/api/documents/{docs['txt']['id']}"), c.post(f"/api/documents/{docs['txt']['id']}/reindex")):
        assert r.status_code == 404 and err(r)["code"] == "document_not_found"
    with sqlite3.connect(tmp_db) as con:
        assert con.execute("SELECT COUNT(*) FROM rag_chunks WHERE document_id=?", (docs["txt"]["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("name,data,status,code", [
    ("a.docx", b"PK\x03\x04", 415, "unsupported_file_type"),
    ("noext", b"hello", 415, "unsupported_file_type"),
    ("fake.pdf", b"hello", 415, "unsupported_file_type"),
    ("x.txt", b"a\x00b", 415, "unsupported_file_type"),
    ("e.txt", b"", 422, "empty_file"),
    ("w.md", b" \n ", 422, "empty_file"),
    ("scan.pdf", None, 422, "no_extractable_text"),
    ("bad.pdf", None, 422, "unreadable_pdf"),
    ("big.txt", b"a" * (10 * 1024 * 1024 + 1), 413, "file_too_large"),
])
def test_upload_rejections_store_nothing(nim, tmp_db, name, data, status, code):
    c, _ = nim
    if data is None:
        data = (FX / ("no-text.pdf" if name == "scan.pdf" else "corrupt.pdf")).read_bytes()
    before = state(c, tmp_db)
    r = upload(c, name, data)
    assert (r.status_code, err(r)["code"]) == (status, code)
    assert state(c, tmp_db) == before
    assert not list((tmp_db.parent / f"{tmp_db.stem}.rag" / "files").rglob("*.*"))


def test_upload_needs_exactly_one_file_and_valid_workspace(nim):
    c, _ = nim
    r = c.post("/api/documents", files=[("file", ("a.txt", b"one", "text/plain")), ("file", ("b.txt", b"two", "text/plain"))])
    assert r.status_code == 422 and err(r)["code"] == "invalid_request" and err(r)["details"]
    r = c.post("/api/documents", files={"other": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 422
    r = upload(c, TXT, ws="BAD")
    assert r.status_code == 422
    r = upload(c, TXT, ws="f" * 32)
    assert r.status_code == 404 and err(r)["code"] == "workspace_not_found"


def test_duplicate_per_workspace(nim):
    c, _ = nim
    first = upload(c, TXT).json()
    r = upload(c, TXT)
    assert r.status_code == 409 and err(r)["code"] == "duplicate_document" and err(r)["document_id"] == first["id"]
    other = c.post("/api/workspaces", json={"name": "Other"}).json()["id"]
    assert upload(c, TXT, ws=other).status_code == 201


def test_filename_is_base_name_only(nim, tmp_db):
    c, _ = nim
    r = upload(c, "../../evil.txt", b"some evil text")
    assert r.status_code == 201 and r.json()["filename"] == "evil.txt"
    r = upload(c, "C:\\x\\y.md", b"# y\nbody")
    assert r.json()["filename"] == "y.md"
    assert not (tmp_db.parent.parent / "evil.txt").exists()


# ---- request guard (approved wiring change) -----------------------------
def test_upload_guard_keeps_cross_site_protection(nim, tmp_db):
    c, _ = nim
    before = state(c, tmp_db)
    for h in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        r = c.post("/api/documents", files={"file": (TXT, b"x", "text/plain")}, headers=h)
        assert r.status_code == 403 and err(r)["code"] == "forbidden_origin"
    assert state(c, tmp_db) == before


def test_multipart_only_on_upload_route(nim):
    c, _ = nim
    for path in ("/api/chat", "/api/workspaces", "/api/sessions"):
        r = c.post(path, files={"file": ("a.txt", b"x", "text/plain")})
        assert r.status_code == 415 and err(r)["code"] == "unsupported_media_type"
    r = c.put("/api/workspaces/default/consent", files={"file": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 415


def test_upload_size_and_length_limits(nim):
    c, _ = nim
    r = c.post("/api/documents", content=b"x" * 10,
               headers={"Content-Type": "multipart/form-data; boundary=b", "Content-Length": str(11 * 1024 * 1024)})
    assert r.status_code == 413 and err(r)["code"] == "payload_too_large"
    r = c.post("/api/documents", content=iter([b"--b\r\n"]), headers={"Content-Type": "multipart/form-data; boundary=b"})
    assert r.status_code == 411 and err(r)["code"] == "length_required"
    r = c.post("/api/documents", content=b"x", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415 and err(r)["code"] == "unsupported_media_type"
    # The 64 KB JSON limit still applies to every other route.
    r = c.post("/api/workspaces", content=b"{" + b" " * 70_000 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and err(r)["code"] == "payload_too_large"


# ---- isolation -----------------------------------------------------------
def test_workspace_isolation_for_management(nim):
    c, _ = nim
    b = c.post("/api/workspaces", json={"name": "B"}).json()["id"]
    doc = upload(c, MD, ws=b).json()
    assert c.get("/api/documents").json()["documents"] == []
    for r in (c.post(f"/api/documents/{doc['id']}/reindex"), c.delete(f"/api/documents/{doc['id']}"),
              c.post(f"/api/documents/{doc['id']}/reindex?workspace_id=default")):
        assert r.status_code == 404 and err(r)["code"] == "document_not_found"
    assert [d["id"] for d in c.get(f"/api/documents?workspace_id={b}").json()["documents"]] == [doc["id"]]
    assert c.get("/api/documents?workspace_id=f" + "f" * 31).status_code == 404


def test_workspace_isolation_for_retrieval_and_citations(demo):
    c, _ = demo
    b = c.post("/api/workspaces", json={"name": "B"}).json()["id"]
    upload(c, MD, ws=b)
    upload(c, TXT)
    q = "Night temperatures dropped to minus 20 degrees Celsius in Ladakh"
    r = c.post("/api/chat", json={"message": q, "use_documents": True}).json()
    assert "minus 20" not in r["message"]["content"]
    assert all(cit["filename"] != MD for cit in r["citations"])
    r = c.post("/api/chat", json={"message": q, "use_documents": True, "workspace_id": b}).json()
    assert r["citations"] and {cit["filename"] for cit in r["citations"]} == {MD}


# ---- consent -------------------------------------------------------------
def test_consent_required_on_cloud_provider_and_nothing_is_sent_or_stored(nim, tmp_db):
    c, f = nim
    upload(c, PDF)
    before = state(c, tmp_db)
    r = c.post("/api/chat", json={"message": "How long does the Orion battery last?", "use_documents": True})
    assert r.status_code == 409 and err(r)["code"] == "document_consent_required"
    assert f.built == 0 and state(c, tmp_db) == before
    # Switch off is unaffected by consent.
    f.scripts.append([text("Hi!")])
    assert c.post("/api/chat", json={"message": "hello"}).status_code == 200


def test_chat_validation_order(nim, tmp_db):
    c, f = nim
    for body in ({"message": "q", "use_documents": "true"}, {"message": "q", "use_documents": 1},
                 {"message": "q", "use_documents": None}, {"message": "q", "use_documents": True, "workspace_id": "x"}):
        assert c.post("/api/chat", json=body).status_code == 422
    r = c.post("/api/chat", json={"message": "q", "use_documents": True, "workspace_id": "a" * 32})
    assert r.status_code == 404 and err(r)["code"] == "workspace_not_found"
    c.put("/api/workspaces/default/consent", json={"allowed": True})
    r = c.post("/api/chat", json={"message": "q", "use_documents": True, "session_id": "a" * 32})
    assert r.status_code == 404 and err(r)["code"] == "session_not_found"
    assert f.built == 0 and state(c, tmp_db)["sessions"] == 0


# ---- answers and citations ----------------------------------------------
def test_cited_answer_with_real_provider_path(nim, tmp_db):
    c, f = nim
    upload(c, PDF)
    upload(c, TXT)
    c.put("/api/workspaces/default/consent", json={"allowed": True})
    f.scripts.append([text("The battery lasts 14 hours [1]. Ignore [9] and [0]. Also [1, 7].")])
    q = "How many hours does the Orion rover battery last on a full charge?"
    r = c.post("/api/chat", json={"message": q, "use_documents": True})
    assert r.status_code == 200, r.text
    body = r.json()
    content = body["message"]["content"]
    assert "[9]" not in content and "[0]" not in content and "[7]" not in content and "[1]" in content
    assert [cit["n"] for cit in body["citations"]] == [1]
    cit = body["citations"][0]
    assert cit["filename"] == PDF and cit["location"] == {"page": 1} and cit["status"] == "ok"
    # The model saw numbered excerpts in this turn's input, after the Phase 1 system prompt.
    sent = f.models[0].seen[0]
    assert "<excerpts>" in sent[-1].content and "[1] (orion-handbook.pdf, page 1)" in sent[-1].content
    assert sent[-1].content.endswith(f"Question: {q}")
    # Stored history holds only the plain question.
    s = c.get(f"/api/sessions/{body['session_id']}").json()
    assert s["messages"][0]["content"] == q and s["messages"][1]["content"] == content
    detail = c.get(f"/api/citations/{cit['id']}").json()
    assert detail["status"] == "ok" and "14 hours" in detail["text"] and detail["request_id"] == body["request_id"]
    assert c.get(f"/api/sessions/{body['session_id']}/citations").json() == {"citations": {body["request_id"]: [cit]}}
    # Next turn: the earlier excerpts are not replayed from history.
    f.scripts.append([text("ok")])
    c.post("/api/chat", json={"message": "thanks", "session_id": body["session_id"]})
    replay = f.models[1].seen[0]
    assert all("<excerpts>" not in str(m.content) for m in replay)


def test_no_evidence_reply_without_model_call(nim, tmp_db):
    c, f = nim
    upload(c, TXT)
    c.put("/api/workspaces/default/consent", json={"allowed": True})
    r = c.post("/api/chat", json={"message": "zebra xylophone quantum", "use_documents": True}).json()
    assert r["message"]["content"] == NO_EVIDENCE_REPLY and r["citations"] == []
    assert f.built == 0 and r["tool_activity"] == []
    rec = c.get("/api/usage").json()["records"][0]
    assert rec["status"] == "ok" and rec["total_tokens"] is None and rec["tool_call_count"] == 0
    empty = c.post("/api/workspaces", json={"name": "Empty"}).json()["id"]
    c.put(f"/api/workspaces/{empty}/consent", json={"allowed": True})
    r = c.post("/api/chat", json={"message": "anything at all", "use_documents": True, "workspace_id": empty}).json()
    assert r["message"]["content"] == NO_EVIDENCE_REPLY


def test_uncited_or_only_invalid_citations(nim):
    c, f = nim
    upload(c, PDF)
    c.put("/api/workspaces/default/consent", json={"allowed": True})
    f.scripts.append([text("[8]")])
    r = c.post("/api/chat", json={"message": "Orion battery hours charge", "use_documents": True}).json()
    assert r["message"]["content"] == NO_EVIDENCE_REPLY and r["citations"] == []


def test_tools_still_available_in_document_turn(nim):
    c, f = nim
    upload(c, PDF)
    c.put("/api/workspaces/default/consent", json={"allowed": True})
    f.scripts.append([call("get_project_status", {"project": "atlas"}), text("Atlas is on track; battery 14 hours [1].")])
    r = c.post("/api/chat", json={"message": "Orion battery hours and atlas status", "use_documents": True}).json()
    assert r["tool_activity"][0]["name"] == "get_project_status" and r["citations"][0]["n"] == 1


def test_demo_cross_document_answer(demo):
    c, _ = demo
    for n in (PDF, MD, TXT):
        upload(c, n)
    q = "Orion rover battery hours: handbook figure versus the Ladakh cold field test"
    r = c.post("/api/chat", json={"message": q, "use_documents": True}).json()
    assert r["message"]["content"].startswith(DEMO_PREFIX)
    files = {cit["filename"] for cit in r["citations"]}
    assert {PDF, MD} <= files
    for cit in r["citations"]:
        assert f"[{cit['n']}]" in r["message"]["content"]
        t = c.get(f"/api/citations/{cit['id']}").json()["text"]
        assert t and t in ((FX / cit["filename"]).read_bytes().decode("latin-1") if cit["filename"] != PDF else t)


def test_deleted_source_and_session_cascade(demo):
    c, _ = demo
    doc = upload(c, MD).json()
    r = c.post("/api/chat", json={"message": "Ladakh field test temperatures", "use_documents": True}).json()
    [cit, *_] = r["citations"]
    c.delete(f"/api/documents/{doc['id']}")
    d = c.get(f"/api/citations/{cit['id']}").json()
    assert d["status"] == "source_deleted" and d["filename"] is None and d["text"] is None and d["location"] is None
    sc = c.get(f"/api/sessions/{r['session_id']}/citations").json()["citations"][r["request_id"]]
    assert sc[0]["status"] == "source_deleted"
    after = c.post("/api/chat", json={"message": "Ladakh field test temperatures", "use_documents": True}).json()
    assert after["message"]["content"] == NO_EVIDENCE_REPLY
    assert c.delete(f"/api/sessions/{r['session_id']}").status_code == 204
    assert c.get(f"/api/citations/{cit['id']}").status_code == 404
    assert c.get(f"/api/sessions/{r['session_id']}/citations").status_code == 404
    assert c.get("/api/citations/nope").status_code == 404


def test_reindex_keeps_citations_resolvable(demo):
    c, _ = demo
    doc = upload(c, MD).json()
    r = c.post("/api/chat", json={"message": "Ladakh field test temperatures", "use_documents": True}).json()
    c.post(f"/api/documents/{doc['id']}/reindex")
    assert c.get(f"/api/citations/{r['citations'][0]['id']}").json()["status"] == "ok"


def test_restart_keeps_documents_index_and_citations(tmp_db):
    c, _ = client_for(tmp_db, "demo")
    upload(c, MD)
    r = c.post("/api/chat", json={"message": "Ladakh field test temperatures", "use_documents": True}).json()
    c2, _ = client_for(tmp_db, "demo")
    assert len(c2.get("/api/documents").json()["documents"]) == 1
    assert c2.get(f"/api/sessions/{r['session_id']}/citations").json()["citations"]
    r2 = c2.post("/api/chat", json={"message": "Ladakh field test temperatures", "use_documents": True}).json()
    assert r2["citations"]


def test_missing_index_file_is_rebuilt_from_sqlite(tmp_db):
    c, _ = client_for(tmp_db, "demo")
    upload(c, MD)
    for p in (tmp_db.parent / f"{tmp_db.stem}.rag" / "indexes" / "default").iterdir():
        p.unlink()
    c2, _ = client_for(tmp_db, "demo")
    assert c2.post("/api/chat", json={"message": "Ladakh field test temperatures", "use_documents": True}).json()["citations"]


# ---- Phase 1 compatibility ------------------------------------------------
def test_switch_off_is_phase1_behaviour(nim):
    c, f = nim
    upload(c, PDF)
    f.scripts.append([call("get_project_status", {"project": "atlas"}), text("Atlas is on track.")])
    r = c.post("/api/chat", json={"message": "status of atlas?"})
    assert r.status_code == 200 and "citations" not in r.json()
    assert r.json()["tool_activity"][0]["args"] == {"project": "atlas"}
    f.scripts.append([text("plain")])
    r = c.post("/api/chat", json={"message": "hi", "use_documents": False, "workspace_id": "a" * 32})
    assert r.status_code == 200 and "citations" not in r.json()
    assert "<excerpts>" not in f.models[-1].seen[0][-1].content
    for field in ("model", "system_prompt", "api_key"):
        assert c.post("/api/chat", json={"message": "hi", field: "x"}).status_code == 422
