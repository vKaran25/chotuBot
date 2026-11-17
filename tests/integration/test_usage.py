from concurrent.futures import ThreadPoolExecutor
import re
from statistics import mean
from threading import Barrier

from qa_shapes import is_timestamp_with_timezone


ZERO = {"requests": 0, "total_tokens": 0, "avg_latency_ms": 0}
RECORD_FIELDS = {
    "request_id", "session_id", "model", "input_tokens", "output_tokens", "total_tokens",
    "latency_ms", "status", "error_code", "tool_call_count", "created_at",
}


def test_USE_AC1_fresh_usage_is_empty(app):
    assert app.usage() == {"totals": ZERO, "records": []}


def test_USE_AC2_records_successful_chat(app):
    chat = app.chat("What is the status of atlas?")
    usage = app.usage()
    assert usage["totals"]["requests"] == 1
    [record] = usage["records"]
    assert record["request_id"] == chat["request_id"]
    assert record["session_id"] == chat["session_id"]
    assert record["status"] == "ok"
    assert record["error_code"] is None
    assert type(record["latency_ms"]) is int and record["latency_ms"] >= 0
    for field in ("model", "tool_call_count"):
        assert record[field] == chat["usage"][field]
    assert is_timestamp_with_timezone(record["created_at"])


def test_USE_AC3_totals_and_newest_first_order(app):
    ids = [app.chat(f"hello number {index}")["request_id"] for index in range(5)]
    usage = app.usage()
    assert usage["totals"]["requests"] == len(ids)
    assert [record["request_id"] for record in usage["records"]] == ids[::-1]
    assert usage["totals"]["total_tokens"] == sum(record["total_tokens"] for record in usage["records"])


def test_USE_AC4_retains_latest_50_but_counts_all_requests(app):
    chats = [app.chat(f"greeting {index}") for index in range(52)]
    usage = app.usage()
    assert usage["totals"]["requests"] == len(chats)
    assert [record["request_id"] for record in usage["records"]] == [
        chat["request_id"] for chat in chats[-50:][::-1]
    ]


def test_USE_AC5_validation_and_lookup_failures_do_not_add_usage(app):
    app.chat()
    before = app.usage()
    for body, status, code in (
        ({"message": ""}, 422, "invalid_request"),
        ({"message": "hello", "session_id": "a" * 32}, 404, "session_not_found"),
    ):
        app.error(app.client.post("/api/chat", json=body), status, code)
        assert app.usage() == before


def test_USE_AC6_provider_failure_adds_one_record(provider_failure):
    app, code, status = provider_failure
    before = app.usage()["totals"]["requests"]
    app.error(app.client.post("/api/chat", json={"message": "hello"}), 503, code)
    usage = app.usage()
    assert usage["totals"]["requests"] == before + 1
    [record] = usage["records"]
    assert record["status"] == status
    assert record["error_code"] == code


def test_USE_AC7_usage_omits_chat_and_credential_data(app):
    message = "What is the status of atlas? ZXYW-671"
    app.chat(message)
    response = app.client.get("/api/usage")
    [record] = app.json(response)["records"]
    assert set(record) == RECORD_FIELDS
    assert message not in response.text
    assert "Beta release" not in response.text
    if app.credentials["NVIDIA_API_KEY"] in response.text:
        raise AssertionError("Usage returned a credential")


def test_USE_AC8_deletion_clears_the_session_reference_but_keeps_usage(app):
    chat = app.chat()
    before = app.usage()
    assert app.client.delete(f"/api/sessions/{chat['session_id']}").status_code == 204
    assert app.usage() == {"totals": before["totals"], "records": [
        {**before["records"][0], "session_id": None}
    ]}


def test_USE_AC9_usage_survives_restart(app):
    app.chat("hello")
    app.chat("What is the status of atlas?")
    before = app.usage()
    app.restart()
    assert app.usage() == before


def test_USE_AC10_usage_has_no_billing_data(app):
    app.chat()
    response = app.client.get("/api/usage")
    assert response.status_code == 200
    assert not re.search(r"money|cost|price|budget|currency|[€₹$]", response.text, re.I)


def test_USE_AC11_tool_usage_has_a_positive_call_count(app):
    chat = app.chat("What is the status of atlas?")
    [record] = app.usage()["records"]
    assert record["request_id"] == chat["request_id"]
    assert record["tool_call_count"] >= 1


def test_USE_EDGE1_latency_is_rounded_integer_average(app):
    """Edge cases and failure modes: average latency, including empty usage."""
    assert type(app.usage()["totals"]["avg_latency_ms"]) is int
    assert app.usage()["totals"]["avg_latency_ms"] == 0
    for index in range(5):
        app.chat(f"greeting {index}")
    usage = app.usage()
    avg = usage["totals"]["avg_latency_ms"]
    assert type(avg) is int
    assert avg == round(mean(record["latency_ms"] for record in usage["records"]))


def test_USE_EDGE2_concurrent_requests_each_get_a_record(app):
    """Edge cases and failure modes: concurrent requests."""
    barrier = Barrier(5)

    def chat(index):
        barrier.wait(timeout=10)
        return app.chat(f"hello {index}")["request_id"]

    with ThreadPoolExecutor(max_workers=5) as pool:
        ids = set(pool.map(chat, range(5)))
    usage = app.usage()
    assert len(ids) == usage["totals"]["requests"] == 5
    assert {record["request_id"] for record in usage["records"]} == ids


def test_USE_EDGE3_records_outlive_conversations_and_restart(app):
    """Edge cases and failure modes: usage outlives a deleted conversation."""
    chat = app.chat()
    assert app.client.delete(f"/api/sessions/{chat['session_id']}").status_code == 204
    app.restart()
    [record] = app.usage()["records"]
    assert record["request_id"] == chat["request_id"]
    assert record["session_id"] is None
