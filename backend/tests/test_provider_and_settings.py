"""Provider wiring (NVIDIA NIM, mocked), allowlist, settings API, secret non-exposure, storage migration."""
from __future__ import annotations

import json
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import SecretStr

from app.main import create_app
from app.models.provider import MissingApiKey, ModelNotAllowed, allowed_models, build_chat_model
from app.security.redaction import scrub
from app.storage import Storage
from tests.conftest import TEST_KEY
from tests.helpers import make_client, text

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_catalog_models_can_be_selected_and_survive_restart(settings):
    demo = settings.model_copy(update={"provider": "demo"})
    with TestClient(create_app(demo, callbacks=[]), base_url="http://localhost") as client:
        models = client.get("/api/settings").json()["models"]
        assert {m["category"] for m in models} == {"fast", "thinking", "flagship"}
        assert len({m["id"] for m in models}) == len(models)
        for model in models:
            response = client.put("/api/settings", json={"model": model["id"]})
            assert response.status_code == 200
            assert response.json()["model"] == model["id"]
    with TestClient(create_app(demo, callbacks=[]), base_url="http://localhost") as client:
        assert client.get("/api/settings").json()["model"] == models[-1]["id"]


def test_build_model_uses_configured_key_and_model(settings):
    m = build_chat_model(settings, MODEL)
    assert type(m).__name__ == "ChatNVIDIA" and m.model == MODEL
    assert m.usage_telemetry_enabled is False
    assert TEST_KEY not in repr(m) and TEST_KEY not in str(m)


def test_missing_key_fails_before_any_network_call(settings):
    for key in (None, SecretStr("   ")):
        s = settings.model_copy(update={"nvidia_api_key": key})
        assert s.key_configured is False
        with pytest.raises(MissingApiKey):
            build_chat_model(s, MODEL)


def test_model_outside_allowlist_rejected(settings):
    with pytest.raises(ModelNotAllowed):
        build_chat_model(settings, "some/unlisted-model")


def test_mocked_provider_request_uses_configured_settings(settings, monkeypatch):
    """Drive the real ChatNVIDIA class with its transport mocked out."""
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    seen = {}

    def fake_generate(self, messages, stop=None, run_manager=None, **kw):
        seen["model"] = self.model
        msg = AIMessage(content="mocked ok", usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    monkeypatch.setattr(ChatNVIDIA, "_generate", fake_generate)
    c = TestClient(create_app(settings, callbacks=[]), base_url="http://localhost")  # default factory => real ChatNVIDIA
    other = next(m.id for m in allowed_models(settings) if m.id != MODEL)
    assert c.put("/api/settings", json={"model": other}).status_code == 200
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200 and r.json()["message"]["content"] == "mocked ok"
    assert seen == {"model": other}
    assert r.json()["usage"]["total_tokens"] == 5


def test_missing_key_via_api_is_503_naming_the_variable(settings):
    s = settings.model_copy(update={"nvidia_api_key": None})
    c = TestClient(create_app(s, callbacks=[]), base_url="http://localhost")
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "missing_api_key"
    assert "NVIDIA_API_KEY" in r.json()["error"]["message"]
    assert c.get("/api/settings").json()["key_configured"] is False
    [rec] = c.get("/api/usage").json()["records"]
    assert rec["status"] == "missing_key"


# ---- settings API ---------------------------------------------------------
def test_settings_show_key_presence_only(settings):
    c, _ = make_client(settings)
    body = c.get("/api/settings")
    d = body.json()
    assert d["key_configured"] is True and d["key_env_var"] == "NVIDIA_API_KEY" and d["tracing_enabled"] is False
    assert d["model"] == MODEL and d["provider"] == "nim"
    assert {m["id"] for m in d["models"]} == {m.id for m in allowed_models(settings)} and len(d["models"]) >= 2
    assert TEST_KEY not in body.text and "nvapi-" not in body.text  # only the env var *name* is exposed
    assert not {"monthly_budget_inr", "usd_to_inr"} & set(d)  # no budget concept any more


def test_put_settings_validates_and_persists_across_restart(settings):
    c, _ = make_client(settings)
    other = next(m.id for m in allowed_models(settings) if m.id != MODEL)
    assert c.put("/api/settings", json={"model": other}).status_code == 200
    bad = c.put("/api/settings", json={"model": "evil/unlisted"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "model_not_supported"
    for body in ({}, {"model": ""}, {"nvidia_api_key": "nvapi-x"}, {"model": MODEL, "monthly_budget_inr": 5}):
        assert c.put("/api/settings", json=body).status_code == 422, body
    c2, _ = make_client(settings)  # restart
    assert c2.get("/api/settings").json()["model"] == other


def test_stale_stored_model_falls_back_to_default(settings):
    c, _ = make_client(settings)
    c.app.state.services.storage.set_setting("model", "openai/gpt-5-nano")  # e.g. left over from an older version
    assert c.get("/api/settings").json()["model"] == MODEL


def test_api_key_cannot_be_set_through_the_api(settings):
    c, _ = make_client(settings)
    assert c.put("/api/settings", json={"nvidia_api_key": "nvapi-attacker"}).status_code == 422


def test_secret_never_appears_in_any_response_or_log(settings, caplog):
    caplog.set_level(logging.DEBUG)
    c, _ = make_client(settings, [RuntimeError(f"401 unauthorized for key {TEST_KEY}")], [text("ok")])
    responses = [
        c.get("/api/health"), c.get("/api/settings"), c.get("/api/sessions"), c.get("/api/usage"),
        c.post("/api/chat", json={"message": "hi"}),  # provider error carrying the key in its message
        c.post("/api/chat", json={"message": ""}),
    ]
    for r in responses:
        assert TEST_KEY not in r.text
    assert TEST_KEY not in caplog.text


def test_nvidia_key_is_scrubbed_from_logs():
    assert TEST_KEY not in scrub(f"401 for {TEST_KEY}") and "nvapi-abcdef123" not in scrub("token nvapi-abcdef123456")


# ---- storage migration ---------------------------------------------------
def test_old_database_with_cost_columns_is_migrated_in_place(tmp_db):
    old = sqlite3.connect(tmp_db)
    old.executescript("""
        CREATE TABLE usage_records (request_id TEXT PRIMARY KEY, session_id TEXT, model TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
            cost_usd REAL NOT NULL DEFAULT 0, cost_inr REAL NOT NULL DEFAULT 0, cost_source TEXT NOT NULL DEFAULT 'none',
            latency_ms INTEGER NOT NULL, status TEXT NOT NULL, error_code TEXT, tool_call_count INTEGER NOT NULL DEFAULT 0,
            month_key TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX idx_usage_month ON usage_records(month_key);
        CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO app_settings VALUES ('monthly_budget_inr', '300'), ('model', 'x/y');
        INSERT INTO usage_records VALUES ('r1', NULL, 'm', 1, 2, 3, 0.1, 9, 'provider', 50, 'ok', NULL, 1, '2026-11', '2026-11-16T00:00:00');
    """)
    old.commit(); old.close()
    st = Storage(tmp_db)  # must not fail, must keep rows
    [row] = st.list_usage()
    assert row["request_id"] == "r1" and row["total_tokens"] == 3 and not {"cost_inr", "month_key"} & set(row)
    assert st.get_setting("monthly_budget_inr") is None and st.get_setting("model") == "x/y"
    st.add_usage({"request_id": "r2", "model": "m", "latency_ms": 1, "status": "ok", "tool_call_count": 0, "created_at": "t"})
    assert st.usage_overview()["totals"]["requests"] == 2
    Storage(tmp_db)  # idempotent on a second start
