import re

import pytest


def test_SET_AC1_settings_shape(app):
    settings = app.settings()
    assert set(settings) == {"provider", "key_configured", "key_env_var", "model", "models", "tracing_enabled"}
    assert settings["provider"] == "demo"
    assert settings["key_env_var"] == "NVIDIA_API_KEY"


def test_SET_AC2_responses_do_not_leak_key_fragments(app):
    key = app.credentials["NVIDIA_API_KEY"]
    chat = app.client.post("/api/chat", json={"message": "hello"})
    session_id = app.json(chat)["session_id"]
    responses = [
        chat, app.client.post("/api/chat", json={}),
        *(app.client.get(path) for path in (
            "/api/settings", "/api/sessions", f"/api/sessions/{session_id}", "/api/usage", "/api/health"
        )),
    ]
    for response in responses:
        if any(key[index:index + 9] in response.text for index in range(len(key) - 8)):
            pytest.fail("A response leaked a credential fragment", pytrace=False)


def test_SET_AC3_key_configured_matches_environment(make_app):
    assert make_app().settings()["key_configured"] is True
    assert make_app(key="").settings()["key_configured"] is False


def test_SET_AC4_active_model_is_listed_and_tool_capable(app):
    settings = app.settings()
    assert settings["models"]
    assert settings["model"] in {model["id"] for model in settings["models"]}
    for model in settings["models"]:
        assert isinstance(model["id"], str) and isinstance(model["label"], str)
        assert model["tool_capable"] is True


def test_SET_AC5_allowed_model_is_saved(app):
    model = app.other_model()
    before = app.settings()
    saved = app.select_model(model)
    assert saved == {**before, "model": model}
    assert app.settings() == saved


def test_SET_AC6_chat_and_usage_use_selected_model(app):
    model = app.other_model()
    app.select_model(model)
    chat = app.chat()
    assert chat["usage"]["model"] == model
    [record] = app.usage()["records"]
    assert record["request_id"] == chat["request_id"]
    assert record["model"] == model


def test_SET_AC7_selected_model_persists_after_restart(app):
    model = app.other_model()
    app.select_model(model)
    app.restart()
    assert app.settings()["model"] == model


def test_SET_AC8_unlisted_model_does_not_change_settings(app):
    before = app.settings()
    model = "qa/unlisted"
    assert model not in {item["id"] for item in before["models"]}
    app.error(app.client.put("/api/settings", json={"model": model}), 422, "model_not_supported")
    assert app.settings() == before


def test_SET_AC9_rejects_missing_or_invalid_model(app):
    for kwargs in ({}, *({"json": value} for value in ({}, {"model": ""}, {"model": 5}, {"model": None}))):
        app.error(app.client.put("/api/settings", **kwargs), 422, "invalid_request")


def test_SET_AC10_rejects_extra_fields_without_changing_settings(app):
    before = app.settings()
    for body in ({"model": before["model"], "nvidia_api_key": "x"}, {"api_key": "x"},
                 {"tracing_enabled": True}, {"monthly_budget_inr": 5}):
        app.error(app.client.put("/api/settings", json=body), 422, "invalid_request")
        assert app.settings() == before


def test_SET_AC11_tracing_is_disabled_and_read_only(app):
    settings = app.settings()
    assert settings["tracing_enabled"] is False
    response = app.client.put("/api/settings", json={"model": settings["model"], "tracing_enabled": True})
    app.error(response, 422, "invalid_request")
    assert app.settings()["tracing_enabled"] is False


def test_SET_AC12_cross_site_put_does_not_change_model(app):
    before = app.settings()
    response = app.client.put("/api/settings", json={"model": app.other_model()},
                              headers={"Origin": "https://evil.example"})
    app.error(response, 403, "forbidden_origin")
    assert app.settings() == before


def test_SET_AC13_has_no_billing_fields(app):
    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert not any(re.search(r"money|cost|price|budget|currency", key, re.I) for key in keys(app.settings()))


def test_SET_EDGE1_model_change_only_affects_subsequent_requests(app):
    """Edge cases and failure modes: changing models preserves earlier history."""
    original = app.chat()
    session_id = original["session_id"]
    history = app.session(session_id)
    [record] = app.usage()["records"]
    model = app.other_model()
    app.select_model(model)
    assert app.session(session_id) == history
    followup = app.chat("another turn", session_id=session_id)
    assert followup["usage"]["model"] == model
    assert app.session(session_id)["messages"][:2] == history["messages"]
    assert app.usage()["records"][-1] == record


def test_SET_EDGE2_last_put_wins(app):
    """Edge cases and failure modes: back-to-back settings updates."""
    original = app.settings()["model"]
    app.select_model(app.other_model())
    app.select_model(original)
    assert app.settings()["model"] == original


def test_SET_EDGE3_key_is_read_only_over_http(make_app):
    """Edge cases and failure modes: configuration owns the API key."""
    app = make_app(key="")
    model = app.settings()["model"]
    assert app.settings()["key_configured"] is False
    for field in ("api_key", "nvidia_api_key", "NVIDIA_API_KEY"):
        response = app.client.put("/api/settings", json={"model": model, field: "qa-placeholder"})
        app.error(response, 422, "invalid_request")
    app.restart()
    assert app.settings()["key_configured"] is False
