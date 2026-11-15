"""P1-T14, T20: OpenRouter BYOK wiring (mocked), allowlist, settings API, secret non-exposure."""
from __future__ import annotations

import json
import logging

import pytest
from pydantic import SecretStr
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.main import create_app
from app.models.provider import MissingApiKey, ModelNotAllowed, build_chat_model
from tests.conftest import TEST_KEY
from tests.helpers import make_client, text


def test_build_model_uses_configured_key_and_model(settings):
    m = build_chat_model(settings, "openai/gpt-oss-20b")
    assert m.model_name == "openai/gpt-oss-20b"
    assert m.openrouter_api_key.get_secret_value() == TEST_KEY
    assert TEST_KEY not in repr(m) and TEST_KEY not in str(m)


def test_missing_key_fails_before_any_network_call(settings):
    s = settings.model_copy(update={"openrouter_api_key": None})
    with pytest.raises(MissingApiKey):
        build_chat_model(s, "openai/gpt-5-nano")
    s2 = settings.model_copy(update={"openrouter_api_key": SecretStr("   ")})
    with pytest.raises(MissingApiKey):
        build_chat_model(s2, "openai/gpt-5-nano")


def test_model_outside_allowlist_rejected(settings):
    with pytest.raises(ModelNotAllowed):
        build_chat_model(settings, "some/unlisted-model")


def test_mocked_provider_request_uses_configured_settings(settings, monkeypatch):
    """Drive the real ChatOpenRouter class with its transport mocked out."""
    from langchain_openrouter import ChatOpenRouter

    seen = {}

    def fake_generate(self, messages, stop=None, run_manager=None, **kw):
        seen["model"] = self.model_name
        seen["key"] = self.openrouter_api_key.get_secret_value()
        msg = AIMessage(content="mocked ok", usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                        response_metadata={"cost": 0.00001})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    monkeypatch.setattr(ChatOpenRouter, "_generate", fake_generate)
    app = create_app(settings, callbacks=[])  # default factory => real ChatOpenRouter
    c = TestClient(app, base_url="http://localhost")
    assert c.put("/api/settings", json={"model": "openai/gpt-oss-20b"}).status_code == 200
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200 and r.json()["message"]["content"] == "mocked ok"
    assert seen == {"model": "openai/gpt-oss-20b", "key": TEST_KEY}


def test_missing_key_via_api_is_503_without_provider_call(settings):
    s = settings.model_copy(update={"openrouter_api_key": None})
    c = TestClient(create_app(s, callbacks=[]), base_url="http://localhost")
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "missing_api_key"
    assert c.get("/api/settings").json()["key_configured"] is False
    [rec] = c.get("/api/usage").json()["records"]
    assert rec["status"] == "missing_key"


# ---- settings API -------------------------------------------------------
def test_settings_show_key_presence_only(settings):
    c, _ = make_client(settings)
    body = c.get("/api/settings")
    d = body.json()
    assert d["key_configured"] is True and d["tracing_enabled"] is False
    assert d["model"] == "openai/gpt-5-nano" and d["monthly_budget_inr"] == 300
    assert {m["id"] for m in d["models"]} == {"openai/gpt-5-nano", "openai/gpt-oss-20b"}
    assert TEST_KEY not in body.text and "sk-or-" not in body.text  # only the env var *name* is exposed


def test_put_settings_validates_and_persists_across_restart(settings):
    c, _ = make_client(settings)
    assert c.put("/api/settings", json={"model": "openai/gpt-oss-20b", "monthly_budget_inr": 150}).status_code == 200
    bad = c.put("/api/settings", json={"model": "evil/unlisted"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "model_not_supported"
    for body in ({}, {"monthly_budget_inr": -1}, {"monthly_budget_inr": "lots"}, {"openrouter_api_key": "sk-x"},
                 {"model": "openai/gpt-5-nano", "usd_to_inr": 1}):
        assert c.put("/api/settings", json=body).status_code == 422, body
    c2, _ = make_client(settings)  # restart
    d = c2.get("/api/settings").json()
    assert d["model"] == "openai/gpt-oss-20b" and d["monthly_budget_inr"] == 150


def test_api_key_cannot_be_set_through_the_api(settings):
    c, _ = make_client(settings)
    r = c.put("/api/settings", json={"openrouter_api_key": "sk-or-v1-attacker"})
    assert r.status_code == 422


def test_secret_never_appears_in_any_response_or_log(settings, caplog):
    caplog.set_level(logging.DEBUG)
    c, _ = make_client(settings, [RuntimeError(f"401 unauthorized for key {TEST_KEY}")], [text("ok")])
    responses = [
        c.get("/api/health"), c.get("/api/settings"), c.get("/api/sessions"), c.get("/api/usage"),
        c.post("/api/chat", json={"message": "hi"}),  # provider error carrying the key in its message
        c.post("/api/chat", json={"message": TEST_KEY}),  # user pastes the key: stored locally, not echoed in errors
        c.post("/api/chat", json={"message": ""}),
    ]
    for r in responses[:5] + responses[6:]:
        assert TEST_KEY not in r.text
    assert TEST_KEY not in caplog.text


# ---- NVIDIA NIM testing provider (ADR 0002) ------------------------------------
NIM_KEY = "nvapi-TEST-SENTINEL-0123456789abcdef"


@pytest.fixture
def nim_settings(tmp_db):
    from app.config import Settings

    return Settings(_env_file=None, CHOTU_PROVIDER="nim", NVIDIA_API_KEY=NIM_KEY, OPENROUTER_API_KEY=TEST_KEY,
                    CHOTU_DB_PATH=tmp_db, CHOTU_DEFAULT_MODEL="openai/gpt-oss-20b")


def test_nim_builds_chatnvidia_with_nvidia_key_and_no_telemetry(nim_settings):
    m = build_chat_model(nim_settings, "openai/gpt-oss-20b")
    assert type(m).__name__ == "ChatNVIDIA" and m.model == "openai/gpt-oss-20b"
    assert m.usage_telemetry_enabled is False
    assert NIM_KEY not in repr(m)


def test_nim_ignores_the_openrouter_key_and_reports_missing(nim_settings):
    s = nim_settings.model_copy(update={"nvidia_api_key": None})
    assert s.key_configured is False and s.key_env_var == "NVIDIA_API_KEY"
    with pytest.raises(MissingApiKey):
        build_chat_model(s, "openai/gpt-oss-20b")


def test_nim_allowlist_is_separate_and_rejects_openrouter_models(nim_settings):
    from app.models.provider import allowed_models

    ids = {m.id for m in allowed_models(nim_settings)}
    assert "openai/gpt-oss-20b" in ids and "openai/gpt-5-nano" not in ids
    with pytest.raises(ModelNotAllowed):
        build_chat_model(nim_settings, "openai/gpt-5-nano")


def test_nim_settings_api_and_model_fallback(nim_settings):
    c, _ = make_client(nim_settings)
    c.app.state.services.storage.set_setting("model", "openai/gpt-5-nano")  # stale choice from another provider
    d = c.get("/api/settings").json()
    assert d["provider"] == "nim" and d["key_env_var"] == "NVIDIA_API_KEY" and d["key_configured"] is True
    assert d["model"] == "openai/gpt-oss-20b"
    assert NIM_KEY not in json.dumps(d) and TEST_KEY not in json.dumps(d)
    assert c.put("/api/settings", json={"model": "openai/gpt-5-nano"}).status_code == 422


def test_nim_missing_key_is_503_naming_the_right_variable(nim_settings):
    s = nim_settings.model_copy(update={"nvidia_api_key": None})
    c = TestClient(create_app(s, callbacks=[]), base_url="http://localhost")
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503 and "NVIDIA_API_KEY" in r.json()["error"]["message"]


def test_nim_key_is_scrubbed_from_logs():
    from app.security.redaction import scrub

    assert NIM_KEY not in scrub(f"401 for {NIM_KEY}") and "nvapi-abcdef123" not in scrub("token nvapi-abcdef123456")
