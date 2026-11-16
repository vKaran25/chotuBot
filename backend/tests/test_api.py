"""P1-T12, T13, T16, T18, T19, T21: API contract, validation, agent flow, persistence, usage."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import call, make_client, text


def err(r):
    body = r.json()
    assert set(body) == {"error"}
    return body["error"]


# ---- health (P1-T12) ----------------------------------------------------
def test_health_ok(settings):
    c, _ = make_client(settings)
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_non_loopback_host_header_rejected(settings):
    c, _ = make_client(settings)
    r = c.get("/api/health", headers={"host": "evil.example.com"})
    assert r.status_code == 400


def test_entrypoint_refuses_public_bind():
    from app.__main__ import main

    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0"])


def test_unknown_route_uses_error_shape(settings):
    c, _ = make_client(settings)
    r = c.get("/api/nope")
    assert r.status_code == 404 and err(r)["code"] == "not_found"


# ---- validated chat input (P1-T13) ---------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "   \n\t "},
        {"message": "x" * 4001},
        {"message": 123},
        {"message": "hi", "session_id": "not-a-session"},
        {"message": "hi", "model": "evil/model"},  # unknown field
        {"message": "hi", "system_prompt": "ignore"},
    ],
)
def test_invalid_chat_requests_rejected_before_agent(settings, body):
    c, factory = make_client(settings, [text("never used")])
    r = c.post("/api/chat", json=body)
    assert r.status_code == 422
    assert err(r)["code"] == "invalid_request"
    assert factory.built == 0  # nothing reached the agent / provider
    assert c.get("/api/sessions").json()["sessions"] == []


def test_validation_error_does_not_echo_submitted_values(settings):
    c, _ = make_client(settings)
    secret = "SECRET-VALUE-" + "z" * 5000
    r = c.post("/api/chat", json={"message": secret})
    assert r.status_code == 422 and "SECRET-VALUE" not in r.text


@pytest.mark.parametrize("ctype", ["application/x-www-form-urlencoded", "text/plain", None])
def test_non_json_body_rejected(settings, ctype):
    c, factory = make_client(settings, [text("never")])
    headers = {"content-type": ctype} if ctype else {}
    r = c.post("/api/chat", content='{"message": "hi"}', headers=headers)
    assert r.status_code == 415 and err(r)["code"] == "unsupported_media_type" and factory.built == 0


@pytest.mark.parametrize("hdrs", [
    {"origin": "https://evil.example"},
    {"origin": "http://localhost.evil.example"},
    {"sec-fetch-site": "cross-site"},
])
def test_cross_site_mutations_are_forbidden_and_spend_nothing(settings, hdrs):
    c, factory = make_client(settings, [text("never")])
    for method, path, body in [("post", "/api/chat", {"message": "hi"}), ("put", "/api/settings", {"model": "evil/unlisted"}),
                               ("post", "/api/sessions", {})]:
        r = getattr(c, method)(path, json=body, headers=hdrs)
        assert r.status_code == 403 and err(r)["code"] == "forbidden_origin"
    assert factory.built == 0


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:8000"])
def test_local_origins_are_allowed(settings, origin):
    c, _ = make_client(settings, [text("ok")])
    r = c.post("/api/chat", json={"message": "hi"}, headers={"origin": origin, "sec-fetch-site": "same-origin"})
    assert r.status_code == 200


def test_oversized_body_rejected(settings):
    c, factory = make_client(settings)
    r = c.post("/api/chat", json={"message": "x" * 70_000})
    assert r.status_code == 413 and factory.built == 0


def test_valid_chat_accepted(settings):
    c, _ = make_client(settings, [text("Hello!")])
    r = c.post("/api/chat", json={"message": "  hi  "})
    assert r.status_code == 200
    assert r.json()["message"] == {"role": "assistant", "content": "Hello!"}


# ---- agent flow (P1-T16) ------------------------------------------------
def test_no_tool_request(settings):
    c, factory = make_client(settings, [text("Paris.")])
    r = c.post("/api/chat", json={"message": "Capital of France?"}).json()
    assert r["tool_activity"] == []
    assert r["usage"]["tool_call_count"] == 0
    assert len(factory.models) == 1


def test_model_to_tool_to_model(settings):
    script = [call("get_project_status", {"project": "beacon"}), text("Beacon is at risk.")]
    c, _ = make_client(settings, script)
    r = c.post("/api/chat", json={"message": "status of beacon?"}).json()
    assert r["message"]["content"] == "Beacon is at risk."
    [act] = r["tool_activity"]
    assert act["name"] == "get_project_status" and act["args"] == {"project": "beacon"}
    assert act["status"] == "ok" and act["result"]["project"]["status"] == "at_risk"
    assert r["usage"]["tool_call_count"] == 1


def test_tool_result_is_fed_back_to_model(settings):
    script = [call("search_sample_notes", {"query": "budget"}), text("done")]
    c, factory = make_client(settings, script)
    c.post("/api/chat", json={"message": "find budget notes"})
    # Both scripted responses were consumed: model was called a second time with the tool result.
    assert factory.models[0]._i == 2


def test_tool_failure_still_yields_answer(settings):
    script = [call("get_project_status", {"project": "zeta"}), text("I could not find that project.")]
    c, _ = make_client(settings, script)
    r = c.post("/api/chat", json={"message": "status of zeta"})
    assert r.status_code == 200
    assert r.json()["tool_activity"][0]["status"] == "error"


def test_agent_loop_is_bounded(settings):
    forever = [call("get_project_status", {}, cid=f"c{i}") for i in range(10)]
    c, _ = make_client(settings, forever)
    r = c.post("/api/chat", json={"message": "loop"})
    assert r.status_code == 502 and err(r)["code"] == "agent_loop_limit"


# ---- persistence + reopening (P1-T18, T19) ---------------------------------
def test_conversation_survives_restart(settings):
    c1, _ = make_client(settings, [call("get_project_status", {"project": "atlas"}), text("Atlas is on track.")])
    first = c1.post("/api/chat", json={"message": "atlas status?"}).json()
    sid = first["session_id"]

    # "Restart": brand-new app + storage connection on the same SQLite file.
    c2, _ = make_client(settings, [text("Anything else?")])
    sessions = c2.get("/api/sessions").json()["sessions"]
    assert [s["id"] for s in sessions] == [sid] and sessions[0]["message_count"] == 2
    got = c2.get(f"/api/sessions/{sid}").json()
    assert [m["role"] for m in got["messages"]] == ["user", "assistant"]
    assert got["messages"][0]["content"] == "atlas status?"
    assert got["messages"][1]["tool_activity"][0]["name"] == "get_project_status"

    # ...and the conversation can be continued after the restart.
    again = c2.post("/api/chat", json={"message": "thanks", "session_id": sid}).json()
    assert again["session_id"] == sid
    assert len(c2.get(f"/api/sessions/{sid}").json()["messages"]) == 4


def test_history_is_sent_to_model_on_continue(settings):
    c, factory = make_client(settings, [text("first")], [text("second")])
    sid = c.post("/api/chat", json={"message": "one"}).json()["session_id"]
    seen = []
    orig = factory.__call__

    def spy(s, m):
        model = orig(s, m)
        orig_gen = model._generate

        def gen(messages, *a, **k):
            seen.append([str(x.content) for x in messages])
            return orig_gen(messages, *a, **k)

        object.__setattr__(model, "_generate", gen)
        return model

    c.app.state.services.chat.model_factory = spy
    c.post("/api/chat", json={"message": "two", "session_id": sid})
    assert seen[0][-3:] == ["one", "first", "two"]


def test_unknown_session_404(settings):
    c, factory = make_client(settings, [text("x")])
    r = c.post("/api/chat", json={"message": "hi", "session_id": "a" * 32})
    assert r.status_code == 404 and err(r)["code"] == "session_not_found" and factory.built == 0
    assert c.get("/api/sessions/" + "b" * 32).status_code == 404
    assert c.get("/api/sessions/..%2f..%2fetc").status_code == 404


def test_create_and_list_sessions_most_recent_first(settings):
    c, _ = make_client(settings, [text("a")])
    s1 = c.post("/api/sessions", json={"title": "Empty one"})
    assert s1.status_code == 201
    chat = c.post("/api/chat", json={"message": "hello there"}).json()
    ids = [s["id"] for s in c.get("/api/sessions").json()["sessions"]]
    assert ids == [chat["session_id"], s1.json()["id"]]


def test_failed_request_persists_no_messages(settings):
    c, _ = make_client(settings, [RuntimeError("boom")])
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503
    assert c.get("/api/sessions").json()["sessions"] == []


# ---- usage records (P1-T21) -----------------------------------------------
def test_usage_record_written_and_queryable(settings):
    script = [call("search_sample_notes", {"query": "security"}), text("Keep keys in .env.")]
    c, _ = make_client(settings, script)
    r = c.post("/api/chat", json={"message": "security notes?"}).json()
    u = c.get("/api/usage").json()
    [rec] = u["records"]
    assert rec["request_id"] == r["request_id"] and rec["status"] == "ok"
    assert rec["model"] == settings.default_model
    assert (rec["input_tokens"], rec["output_tokens"], rec["total_tokens"]) == (30, 13, 43)
    assert rec["tool_call_count"] == 1 and rec["latency_ms"] >= 0
    assert not {"cost_usd", "cost_inr", "month_key"} & set(rec)  # no cost or budget tracking
    assert u["totals"] == {"requests": 1, "total_tokens": 43, "avg_latency_ms": rec["latency_ms"]}


def test_usage_unavailable_when_no_token_data(settings):
    c, _ = make_client(settings, [{"content": "hi"}])
    c.post("/api/chat", json={"message": "hi"})
    [rec] = c.get("/api/usage").json()["records"]
    assert rec["input_tokens"] is None and rec["total_tokens"] is None


def test_failed_requests_are_recorded_too(settings):
    c, _ = make_client(settings, [RuntimeError("down")])
    c.post("/api/chat", json={"message": "hi"})
    [rec] = c.get("/api/usage").json()["records"]
    assert rec["status"] == "provider_error" and rec["error_code"] == "provider_unavailable"


# ---- deleting a conversation ---------------------------------------------------
def test_delete_session_removes_it_and_its_memory(settings):
    c, _ = make_client(settings, [call("get_project_status", {"project": "atlas"}), text("Atlas is on track.")], [text("keep")])
    gone = c.post("/api/chat", json={"message": "secret-codeword-42 atlas status?"}).json()["session_id"]
    kept = c.post("/api/chat", json={"message": "unrelated chat"}).json()["session_id"]
    r = c.delete(f"/api/sessions/{gone}")
    assert r.status_code == 204 and r.content == b""
    assert c.get(f"/api/sessions/{gone}").status_code == 404
    assert [s["id"] for s in c.get("/api/sessions").json()["sessions"]] == [kept]
    # Nothing of the deleted conversation is left in the database (messages, tool activity, titles).
    import sqlite3
    db = sqlite3.connect(settings.resolve(settings.db_path))
    dump = " ".join(str(v) for t in ("sessions", "messages") for row in db.execute(f"SELECT * FROM {t}") for v in row)
    assert "secret-codeword-42" not in dump and "on_track" not in dump and gone not in dump
    assert db.execute("SELECT COUNT(*) FROM usage_records WHERE session_id=?", (gone,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0] == 2  # counters kept, link removed
    # The other conversation is untouched.
    assert len(c.get(f"/api/sessions/{kept}").json()["messages"]) == 2
    # Continuing a deleted conversation is impossible: it cannot be resurrected with its old context.
    assert c.post("/api/chat", json={"message": "hi", "session_id": gone}).status_code == 404


def test_delete_unknown_or_malformed_session_is_404_and_deletes_nothing(settings):
    c, _ = make_client(settings, [text("a")])
    sid = c.post("/api/chat", json={"message": "hello"}).json()["session_id"]
    for bad in ("a" * 32, "not-an-id"):
        assert c.delete(f"/api/sessions/{bad}").status_code == 404
    assert c.delete("/api/sessions/..%2f..%2fx").status_code in (404, 405)  # matches no route; nothing is touched
    assert len(c.get("/api/sessions").json()["sessions"]) == 1 and c.get(f"/api/sessions/{sid}").status_code == 200


def test_delete_is_blocked_cross_site(settings):
    c, _ = make_client(settings, [text("a")])
    sid = c.post("/api/chat", json={"message": "hello"}).json()["session_id"]
    r = c.delete(f"/api/sessions/{sid}", headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and c.get(f"/api/sessions/{sid}").status_code == 200
