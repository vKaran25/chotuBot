"""P1-T23: LangSmith is off by default and redacted when opted in; secrets never reach logs."""
from __future__ import annotations

import json
import logging
import os

import pytest

from app.config import Settings
from app.models.fakes import ScriptedChatModel
from app.observability.tracing import configure_tracing
from app.security.redaction import REDACTED, install_log_scrubber, redact_for_trace, scrub
from tests.conftest import TEST_KEY


def test_tracing_is_off_by_default(settings):
    assert settings.langsmith_enabled is False
    assert configure_tracing(settings) == []


def test_ambient_tracing_env_is_forced_off(settings, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    assert configure_tracing(settings) == []
    assert os.environ["LANGSMITH_TRACING"] == "false" and os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_opt_in_without_key_stays_off(tmp_db):
    s = Settings(_env_file=None, CHOTU_LANGSMITH_ENABLED=True, CHOTU_DB_PATH=tmp_db)
    assert configure_tracing(s) == []


def test_default_request_makes_no_external_trace_calls(settings, monkeypatch):
    import requests

    calls = []
    monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: calls.append((a, k)))
    from tests.helpers import make_client, text

    c, _ = make_client(settings, [text("hello")], callbacks=None)
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert calls == []


def test_redact_for_trace_removes_all_text_but_keeps_numbers():
    payload = {
        "messages": [{"type": "human", "content": "my password is hunter2"}],
        "tool_args": {"query": "secret plan", "n": 3},
        "key": TEST_KEY,
        "usage": {"input_tokens": 10, "cost": 0.001},
        "flag": True,
    }
    out = redact_for_trace(payload)
    dumped = json.dumps(out)
    for leaked in ("hunter2", "secret plan", TEST_KEY, "human"):
        assert leaked not in dumped
    assert out["usage"] == {"input_tokens": 10, "cost": 0.001} and out["tool_args"]["n"] == 3 and out["flag"] is True


def test_opt_in_tracer_redacts_what_it_would_send(tmp_db, monkeypatch):
    """Opt in with a fake LangSmith key and capture every outbound HTTP body."""
    import requests

    sent: list[bytes] = []

    class Resp:
        status_code, text, headers = 200, "{}", {}
        content = b"{}"

        def json(self):
            return {}

        def raise_for_status(self):
            return None

    def fake_request(self, method, url, *a, **kw):
        body = kw.get("data") or kw.get("json") or b""
        if hasattr(body, "to_string"):  # requests_toolbelt multipart encoder
            body = body.to_string()
        sent.append(body if isinstance(body, bytes) else json.dumps(body, default=str).encode())
        sent.append(url.encode())
        return Resp()

    monkeypatch.setattr(requests.Session, "request", fake_request)
    s = Settings(_env_file=None, CHOTU_LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="lsv2_pt_FAKEFAKEFAKE1234", CHOTU_DB_PATH=tmp_db)
    callbacks = configure_tracing(s)
    assert len(callbacks) == 1
    model = ScriptedChatModel(script=[{"content": "TOP-SECRET-ANSWER-42"}])
    model.invoke("TOP-SECRET-QUESTION-17", config={"callbacks": callbacks})
    callbacks[0].client.flush()
    blob = b"".join(sent)
    assert b"TOP-SECRET" not in blob
    assert b"lsv2_pt_FAKEFAKEFAKE1234" not in blob.replace(b"x-api-key", b"")


# ---- log scrubbing ---------------------------------------------------------
def test_scrub_function():
    assert TEST_KEY not in scrub(f"error with {TEST_KEY} here")
    assert "sk-or-v1-abcdef123456" not in scrub("token sk-or-v1-abcdef123456")
    assert "Bearer abcdefghijkl" not in scrub("Authorization: Bearer abcdefghijkl")
    assert scrub("nothing sensitive") == "nothing sensitive"


def test_log_scrubber_cleans_every_logger(caplog):
    install_log_scrubber([TEST_KEY])
    caplog.set_level(logging.INFO)
    logging.getLogger("uvicorn.error").warning("upstream said %s", TEST_KEY)
    logging.getLogger("anything").error(f"fstring {TEST_KEY}")
    assert TEST_KEY not in caplog.text and REDACTED in caplog.text
