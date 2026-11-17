from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from qa_shapes import is_session_id


LONG_TITLE = "hello  \n world " + "abc " * 23


def test_CHAT_AC1_new_chat_response(app):
    data = app.chat()
    assert is_session_id(data["session_id"])
    assert isinstance(data["request_id"], str) and data["request_id"].strip()
    assert data["message"]["role"] == "assistant"
    assert isinstance(data["message"]["content"], str) and data["message"]["content"].strip()
    assert isinstance(data["tool_activity"], list)
    assert set(data["usage"]) == {
        "model", "input_tokens", "output_tokens", "total_tokens", "latency_ms", "tool_call_count"
    }


def test_CHAT_AC2_titles_and_lists_the_exchange(app):
    session_id = app.chat(LONG_TITLE)["session_id"]
    [session] = app.sessions()
    assert session["id"] == session_id
    assert session["message_count"] == 2
    assert session["title"] == " ".join(LONG_TITLE.split())[:60]


def test_CHAT_AC3_continues_existing_conversation(app):
    session_id = app.chat("hello")["session_id"]
    assert app.chat("again", session_id=session_id)["session_id"] == session_id
    session = app.session(session_id)
    assert session["message_count"] == 4
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"] * 2
    assert [m["content"] for m in session["messages"][::2]] == ["hello", "again"]


def test_CHAT_AC4_trims_outer_whitespace(app):
    session_id = app.chat("  \n hello  world \n ")["session_id"]
    assert app.session(session_id)["messages"][0]["content"] == "hello  world"


def test_CHAT_AC5_message_length_after_trimming(app):
    message = "ab" * 2000
    app.chat(f" \n{message}\t ")
    app.error(app.client.post("/api/chat", json={"message": message + "a"}), 422, "invalid_request")


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": " \n\t"}, {"message": 1},
                                  {"message": []}, {"message": None}])
def test_CHAT_AC6_rejects_invalid_messages_without_saving(app, body):
    app.error(app.client.post("/api/chat", json=body), 422, "invalid_request")
    assert app.sessions() == []
    assert app.usage()["records"] == []


def test_CHAT_AC7_rejects_unknown_fields_without_saving(app):
    before = app.state()
    for field in ("model", "system_prompt", "api_key"):
        response = app.client.post("/api/chat", json={"message": "hello", field: "example"})
        app.error(response, 422, "invalid_request")
    assert app.state() == before


def test_CHAT_AC8_rejects_malformed_session_ids(app):
    for value in ("a" * 31, "A" * 32, "z" * 32, 7):
        app.error(app.client.post("/api/chat", json={"message": "hello", "session_id": value}),
                  422, "invalid_request")


def test_CHAT_AC9_unknown_session_is_not_created(app):
    response = app.client.post("/api/chat", json={"message": "hello", "session_id": "a" * 32})
    app.error(response, 404, "session_not_found")
    assert app.sessions() == []


def test_CHAT_AC10_null_starts_new_conversation(app):
    first = app.chat()
    second = app.chat(session_id=None)
    assert first["session_id"] != second["session_id"]
    assert {item["id"]: item["message_count"] for item in app.sessions()} == {
        first["session_id"]: 2, second["session_id"]: 2
    }


def test_CHAT_AC11_missing_key_is_reported_without_saving(make_app):
    app = make_app("nim", "")
    before = app.usage()["totals"]["requests"]
    error = app.error(app.client.post("/api/chat", json={"message": "hello"}), 503, "missing_api_key")
    assert "NVIDIA_API_KEY" in error["message"]
    assert app.sessions() == []
    usage = app.usage()
    assert usage["totals"]["requests"] == before + 1
    [record] = usage["records"]
    assert record["status"] == "missing_key"


def test_CHAT_AC12_invalid_key_is_reported_without_saving(invalid_provider):
    app = invalid_provider
    before = app.usage()["totals"]["requests"]
    app.error(app.client.post("/api/chat", json={"message": "hello"}), 503, "provider_unavailable")
    assert app.sessions() == []
    usage = app.usage()
    assert usage["totals"]["requests"] == before + 1
    [record] = usage["records"]
    assert record["status"] == "provider_error"


def test_CHAT_AC13_failed_chat_does_not_echo_configured_key(invalid_provider):
    app = invalid_provider
    response = app.client.post("/api/chat", json={"message": "hello"})
    app.error(response, 503, "provider_unavailable")
    key = app.credentials["NVIDIA_API_KEY"]
    if key in response.text:
        pytest.fail("The failed chat returned the configured key", pytrace=False)


def test_CHAT_AC14_usage_matches_tools_and_settings(app):
    for text in ("hello", "What is the status of atlas?"):
        data = app.chat(text)
        assert data["usage"]["tool_call_count"] == len(data["tool_activity"])
        assert data["usage"]["model"] == app.settings()["model"]


def test_CHAT_AC15_identical_requests_are_both_saved(app):
    first = app.chat("same text")
    session_id = first["session_id"]
    second = app.chat("same text", session_id=session_id)
    assert first["request_id"] != second["request_id"]
    messages = app.session(session_id)["messages"]
    assert len(messages) == 4
    assert [m["content"] for m in messages[::2]] == ["same text", "same text"]


def test_CHAT_AC16_preserves_unicode(app):
    message = "नमस्ते café 🙂"
    session_id = app.chat(message)["session_id"]
    assert app.session(session_id)["messages"][0]["content"] == message


def test_CHAT_AC17_stores_returned_assistant_reply_and_tools(app):
    data = app.chat("What is the status of atlas?")
    user, assistant = app.session(data["session_id"])["messages"]
    assert user["role"] == "user"
    assert user["tool_activity"] == []
    assert assistant["role"] == "assistant"
    assert assistant["content"] == data["message"]["content"]
    assert assistant["tool_activity"] == data["tool_activity"]


def simultaneous_chats(app, **fields):
    messages = [f"concurrent greeting {i}" for i in range(5)]
    barrier = Barrier(len(messages))

    def send(message):
        barrier.wait(timeout=10)
        return app.chat(message, **fields)

    with ThreadPoolExecutor(max_workers=len(messages)) as pool:
        return dict(zip(messages, pool.map(send, messages), strict=True))


def test_CHAT_AC18_concurrent_new_chats_have_distinct_ids(app):
    replies = simultaneous_chats(app)
    assert len({r["session_id"] for r in replies.values()}) == len(replies)


def test_CHAT_AC19_concurrent_chats_store_adjacent_exchange_pairs(app):
    session_id = app.chat("seed")["session_id"]
    replies = simultaneous_chats(app, session_id=session_id)
    session = app.session(session_id)
    assert session["message_count"] == 2 + 2 * len(replies)
    assert {r["session_id"] for r in replies.values()} == {session_id}
    added = session["messages"][2:]
    assert [m["role"] for m in added] == ["user", "assistant"] * len(replies)
    assert {
        user["content"]: assistant["content"]
        for user, assistant in zip(added[::2], added[1::2], strict=True)
    } == {text: reply["message"]["content"] for text, reply in replies.items()}


def test_CHAT_EDGE1_failed_validation_or_lookup_leaves_no_partial_exchange(app):
    """Edge cases and failure modes: failed requests do not half-save."""
    session_id = app.chat()["session_id"]
    before = app.sessions(), app.session(session_id)
    for message, target, status, code in (
        ("", session_id, 422, "invalid_request"),
        ("hello", "a" * 32, 404, "session_not_found"),
    ):
        response = app.client.post("/api/chat", json={"message": message, "session_id": target})
        app.error(response, status, code)
        assert (app.sessions(), app.session(session_id)) == before


def test_CHAT_EDGE3_accepts_4000_repeated_characters(app):
    """Edge cases and failure modes: a repeated-character message."""
    data = app.chat("x" * 4000)
    assert app.session(data["session_id"])["messages"][0]["content"] == "x" * 4000


def test_CHAT_EDGE4_oversized_body_precedes_validation(app):
    """Edge cases and failure modes: oversized request body."""
    before = app.state()
    response = app.client.post("/api/chat", json={"unexpected": "x" * 65536})
    app.error(response, 413, "payload_too_large")
    assert app.state() == before
