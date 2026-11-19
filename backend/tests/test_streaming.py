"""Streaming chat (owner-approved 2026-11-19): POST /api/chat with Accept: text/event-stream."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from app.config import Settings
from app.models.fakes import ProviderOutage, ScriptedChatModel
from tests.conftest import TEST_KEY
from tests.helpers import call, make_client, text
from tests.test_rag_api import PDF, client_for, upload

SSE = {"Accept": "text/event-stream"}
CHAT_KEYS = {"session_id", "request_id", "message", "tool_activity", "usage"}


def events(resp) -> list[dict]:
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in resp.text.split("\n\n"):
        block = block.strip()
        if block:
            assert block.startswith("data: ")
            out.append(json.loads(block[len("data: "):]))
    return out


def preview(evs: list[dict]) -> str:
    buf = ""
    for e in evs:
        if e["type"] == "delta":
            buf += e["text"]
        elif e["type"] == "reset":
            buf = ""
    return buf


def test_stream_text_reply_and_persistence(settings):
    c, f = make_client(settings, [text("Hello there!")])
    evs = events(c.post("/api/chat", json={"message": "hi"}, headers=SSE))
    assert evs[-1]["type"] == "done" and [e["type"] for e in evs[:-1]] == ["delta"] * (len(evs) - 1)
    res = evs[-1]["response"]
    assert CHAT_KEYS <= set(res) and "citations" not in res
    assert res["message"] == {"role": "assistant", "content": "Hello there!"}
    assert preview(evs) == "Hello there!"
    s = c.get(f"/api/sessions/{res['session_id']}").json()
    assert [m["content"] for m in s["messages"]] == ["hi", "Hello there!"]
    assert c.get("/api/usage").json()["records"][0]["status"] == "ok"


class StreamingScript(ScriptedChatModel):
    """Like ScriptedChatModel but streams: text in two chunks, then any tool calls as a tool-call chunk."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._generate(messages).generations[0].message
        text_ = str(msg.content)
        for part in (text_[: len(text_) // 2], text_[len(text_) // 2:]):
            if part:
                c = AIMessageChunk(content=part)
                if run_manager:
                    run_manager.on_llm_new_token(part, chunk=ChatGenerationChunk(message=c))
                yield ChatGenerationChunk(message=c)
        if msg.tool_calls:
            tc = AIMessageChunk(content="", tool_call_chunks=[
                {"name": t["name"], "args": json.dumps(t["args"]), "id": t["id"], "index": i}
                for i, t in enumerate(msg.tool_calls)])
            yield ChatGenerationChunk(message=tc)


def test_stream_resets_text_written_before_a_tool_call(settings):
    from app.main import create_app
    from fastapi.testclient import TestClient
    scripts = [[call("get_project_status", {"project": "atlas"}, content="Let me check."), text("Atlas is on track.")]]
    app = create_app(settings, model_factory=lambda s, m: StreamingScript(script=scripts.pop(0)), callbacks=[])
    c = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    evs = events(c.post("/api/chat", json={"message": "status of atlas"}, headers=SSE))
    kinds = [e["type"] for e in evs]
    assert kinds.index("reset") > kinds.index("delta")
    assert preview(evs) == "Atlas is on track."
    assert evs[-1]["response"]["tool_activity"][0]["name"] == "get_project_status"


def test_non_streaming_model_never_previews_text_sent_with_a_tool_call(settings):
    c, _ = make_client(settings, [call("get_project_status", {"project": "atlas"}, content="Let me check."),
                                  text("Atlas is on track.")])
    evs = events(c.post("/api/chat", json={"message": "status of atlas"}, headers=SSE))
    assert preview(evs) == "Atlas is on track." and "Let me check." not in json.dumps(evs[:-1])


def test_without_accept_header_json_is_unchanged(settings):
    c, _ = make_client(settings, [text("Hello!")])
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.headers["content-type"].startswith("application/json") and set(r.json()) == CHAT_KEYS


def test_validation_and_lookup_errors_are_json_before_streaming(settings):
    c, f = make_client(settings)
    r = c.post("/api/chat", json={"message": ""}, headers=SSE)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = c.post("/api/chat", json={"message": "x", "session_id": "a" * 32}, headers=SSE)
    assert r.status_code == 404 and r.json()["error"]["code"] == "session_not_found"
    assert f.built == 0


def test_missing_key_is_json_503_and_recorded(tmp_db):
    s = Settings(_env_file=None, NVIDIA_API_KEY="", CHOTU_PROVIDER="nim", CHOTU_DB_PATH=tmp_db)
    from app.main import create_app
    from fastapi.testclient import TestClient
    c = TestClient(create_app(s, callbacks=[]), base_url="http://localhost", raise_server_exceptions=False)
    r = c.post("/api/chat", json={"message": "hi"}, headers=SSE)
    assert r.status_code == 503 and r.json()["error"]["code"] == "missing_api_key"
    assert c.get("/api/usage").json()["records"][0]["status"] == "missing_key"
    assert c.get("/api/sessions").json() == {"sessions": []}


def test_provider_failure_mid_stream_is_an_error_event_and_stores_nothing(settings):
    c, _ = make_client(settings, [ProviderOutage("down")])
    evs = events(c.post("/api/chat", json={"message": "hi"}, headers=SSE))
    assert evs[-1] == {"type": "error", "error": {"code": "provider_unavailable",
                                                  "message": "The model provider is unavailable or returned an error."}}
    assert c.get("/api/sessions").json() == {"sessions": []}
    assert c.get("/api/usage").json()["records"][0]["status"] == "provider_error"
    assert TEST_KEY not in json.dumps(evs)


def test_document_question_streams_with_citations(tmp_db):
    c, f = client_for(tmp_db, "nim")
    upload(c, PDF)
    body = {"message": "How many hours does the Orion rover battery last?", "use_documents": True}
    r = c.post("/api/chat", json=body, headers=SSE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "document_consent_required"
    c.put("/api/workspaces/default/consent", json={"allowed": True})
    f.scripts.append([text("It lasts 14 hours [1]. Also [7].")])
    evs = events(c.post("/api/chat", json=body, headers=SSE))
    res = evs[-1]["response"]
    assert res["message"]["content"] == "It lasts 14 hours [1]. Also."
    assert [ci["n"] for ci in res["citations"]] == [1]
    assert c.get(f"/api/citations/{res['citations'][0]['id']}").json()["status"] == "ok"


def test_document_question_without_evidence_streams_fixed_reply(tmp_db):
    c, f = client_for(tmp_db, "demo")
    upload(c, PDF)
    evs = events(c.post("/api/chat", json={"message": "zebra xylophone", "use_documents": True}, headers=SSE))
    assert evs[-1]["response"]["citations"] == [] and f.built == 0
    assert preview(evs) == evs[-1]["response"]["message"]["content"]
