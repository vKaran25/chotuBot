from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from qa_shapes import is_session_id, is_timestamp_with_timezone


UNKNOWN_IDS = ["a" * 32, "not-an-id", "A" * 32, "a" * 31]


def test_SESS_AC1_fresh_database_is_empty(app):
    assert app.get("/api/sessions") == {"sessions": []}


def test_SESS_AC2_creates_empty_default_session(app):
    created = app.create()
    assert is_session_id(created["id"])
    assert created["title"] == "New conversation"
    assert type(created["message_count"]) is int and created["message_count"] == 0
    for field in ("created_at", "updated_at"):
        assert is_timestamp_with_timezone(created[field])
    assert app.sessions() == [created]
    assert app.session(created["id"]) == {**created, "messages": []}


@pytest.mark.parametrize("title", ["My notes", " " + "x" * 100 + " ", "x" * 101, "", " \t "])
def test_SESS_AC3_validates_trimmed_title_length(app, title):
    if 1 <= len(title.strip()) <= 100:
        assert app.create(title=title)["title"] == title.strip()
    else:
        app.error(app.client.post("/api/sessions", json={"title": title}), 422, "invalid_request")


def test_SESS_AC4_rejects_missing_non_object_and_extra_fields(app):
    for kwargs in ({}, {"json": []}, {"json": "x"}, {"json": {"unknown": True}}):
        app.error(app.client.post("/api/sessions", **kwargs), 422, "invalid_request")


def test_SESS_AC5_first_chat_replaces_default_but_preserves_custom_title(app):
    default = app.create()["id"]
    custom = app.create(title="My own title")["id"]
    text = "hello  \n world " + "abcd " * 20
    for session_id, expected in ((default, " ".join(text.split())[:60]), (custom, "My own title")):
        app.chat(text, session_id=session_id)
        assert app.session(session_id)["title"] == expected


def test_SESS_AC6_orders_by_most_recent_update(app):
    ids = [app.chat(f"greeting {i}")["session_id"] for i in range(3)]
    expected = ids[::-1]
    assert [session["id"] for session in app.sessions()] == expected
    for index, session_id in enumerate(ids * 2):
        app.chat(f"new turn {index}", session_id=session_id)
        expected.remove(session_id)
        expected.insert(0, session_id)
        assert [session["id"] for session in app.sessions()] == expected


def test_SESS_AC7_returns_complete_chronological_messages(app):
    first_text = "What is the status of atlas?"
    second_text = "Search my notes for security"
    first = app.chat(first_text)
    second = app.chat(second_text, session_id=first["session_id"])
    session = app.session(first["session_id"])
    messages = session["messages"]
    assert session["message_count"] == len(messages) == 4
    assert [message["role"] for message in messages] == ["user", "assistant"] * 2
    assert [message["content"] for message in messages[::2]] == [first_text, second_text]
    assert [message["tool_activity"] for message in messages[1::2]] == [
        first["tool_activity"], second["tool_activity"]
    ]
    timestamps = [datetime.fromisoformat(message["created_at"]) for message in messages]
    assert all(timestamp.utcoffset() is not None for timestamp in timestamps)
    assert timestamps == sorted(timestamps)
    assert all(type(message["id"]) is int for message in messages)


@pytest.mark.parametrize("session_id", UNKNOWN_IDS)
def test_SESS_AC8_unknown_or_invalid_id_returns_404(app, session_id):
    app.error(app.client.get(f"/api/sessions/{session_id}"), 404, "session_not_found")


def test_SESS_AC9_persists_history_and_can_continue_after_restart(app):
    session_id = app.chat("What is the status of atlas?")["session_id"]
    app.chat("next turn", session_id=session_id)
    before = app.sessions(), app.session(session_id)
    app.restart()
    assert (app.sessions(), app.session(session_id)) == before
    assert app.chat("after restarting", session_id=session_id)["session_id"] == session_id
    assert app.session(session_id)["message_count"] == 6


def test_SESS_AC10_delete_removes_and_prevents_continuing(app):
    session_id = app.chat()["session_id"]
    response = app.client.delete(f"/api/sessions/{session_id}")
    assert response.status_code == 204 and response.content == b""
    app.error(app.client.get(f"/api/sessions/{session_id}"), 404, "session_not_found")
    assert all(session["id"] != session_id for session in app.sessions())
    response = app.client.post("/api/chat", json={"message": "hello", "session_id": session_id})
    app.error(response, 404, "session_not_found")


def test_SESS_AC11_delete_leaves_other_conversations_unchanged(app):
    a = app.chat("hello")["session_id"]
    b = app.chat("hi there")["session_id"]
    before = app.session(b)
    assert app.client.delete(f"/api/sessions/{a}").status_code == 204
    assert app.session(b) == before


def test_SESS_AC12_deleted_text_is_absent_from_public_gets(app):
    text = "vanishing greeting QAX927"
    deleted = app.chat(text)
    other = app.chat("another greeting")["session_id"]
    assert app.client.delete(f"/api/sessions/{deleted['session_id']}").status_code == 204
    for path in ("/api/sessions", f"/api/sessions/{other}", "/api/usage", "/api/settings",
                 "/api/health", f"/api/sessions/{deleted['session_id']}"):
        response = app.client.get(path)
        assert text not in response.text
        assert deleted["message"]["content"] not in response.text


def test_SESS_AC13_delete_unknown_malformed_or_already_deleted_id(app):
    deleted = app.chat()["session_id"]
    other = app.chat("keep this")["session_id"]
    assert app.client.delete(f"/api/sessions/{deleted}").status_code == 204
    before = app.state(), app.session(other)
    for session_id in [*UNKNOWN_IDS, deleted]:
        app.error(app.client.delete(f"/api/sessions/{session_id}"), 404, "session_not_found")
        assert (app.state(), app.session(other)) == before


def test_SESS_AC14_cross_site_delete_keeps_conversation(app):
    session_id = app.chat()["session_id"]
    before = app.session(session_id)
    response = app.client.delete(f"/api/sessions/{session_id}", headers={"Origin": "https://evil.example"})
    app.error(response, 403, "forbidden_origin")
    assert app.session(session_id) == before


def test_SESS_AC15_delete_keeps_usage_totals(app):
    session_id = app.chat()["session_id"]
    before = app.usage()["totals"]
    assert app.client.delete(f"/api/sessions/{session_id}").status_code == 204
    after = app.usage()["totals"]
    for key in ("requests", "total_tokens"):
        assert after[key] == before[key]


def test_SESS_AC16_deleted_conversation_stays_gone_after_restart(app):
    session_id = app.chat()["session_id"]
    assert app.client.delete(f"/api/sessions/{session_id}").status_code == 204
    app.restart()
    assert app.sessions() == []
    app.error(app.client.get(f"/api/sessions/{session_id}"), 404, "session_not_found")


def test_SESS_EDGE1_unused_conversations_remain_empty(app):
    """Edge cases and failure modes: unused conversations remain empty."""
    empty = app.create()["id"]
    app.chat()
    app.restart()
    [item] = [session for session in app.sessions() if session["id"] == empty]
    assert item["message_count"] == 0
    assert app.session(empty)["messages"] == []


def test_SESS_EDGE2_uppercase_variant_is_invalid(app):
    """Edge cases and failure modes: IDs are case-sensitive."""
    session_id = app.chat()["session_id"]
    if session_id.isdigit():
        session_id = app.chat()["session_id"]
    assert session_id != session_id.upper()
    app.error(app.client.get(f"/api/sessions/{session_id.upper()}"), 404, "session_not_found")
    app.error(app.client.delete(f"/api/sessions/{session_id.upper()}"), 404, "session_not_found")
    assert app.session(session_id)["id"] == session_id


def test_SESS_EDGE3_followup_does_not_change_title(app):
    """Edge cases and failure modes: only the first message determines the title."""
    session_id = app.chat("first greeting")["session_id"]
    app.chat("completely different", session_id=session_id)
    assert app.session(session_id)["title"] == "first greeting"


def test_SESS_EDGE4_conversation_list_has_no_50_item_truncation(app):
    """Edge cases and failure modes: finite sample of the unbounded conversation list."""
    ids = {app.create()["id"] for _ in range(51)}
    app.restart()
    assert {session["id"] for session in app.sessions()} == ids


def test_SESS_EDGE5_concurrent_create_returns_distinct_ids(app):
    """Edge cases and failure modes: concurrent conversation creation."""
    barrier = Barrier(5)

    def create(index):
        barrier.wait(timeout=10)
        return app.create(title=f"Greeting {index}")["id"]

    with ThreadPoolExecutor(max_workers=5) as pool:
        ids = set(pool.map(create, range(5)))
    assert len(ids) == 5
    assert {session["id"] for session in app.sessions()} == ids
