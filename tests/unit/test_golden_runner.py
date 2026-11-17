import importlib.util
import json
from pathlib import Path

import httpx
import pytest


RUNNER_PATH = Path(__file__).resolve().parents[2] / "eval-datasets/chat-answering/run.py"
spec = importlib.util.spec_from_file_location("chotu_golden_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def reply(session_id, *, model="test-model", activity=None):
    return httpx.Response(200, json={
        "session_id": session_id,
        "request_id": "request-stub",
        "message": {"content": "A sample answer."},
        "tool_activity": activity or [],
        "usage": {"model": model},
    })


def test_approved_cases_are_ready_and_pending_cases_are_rejected(tmp_path):
    cases, digest = runner.load_cases(runner.DATASET)
    assert cases and len(digest) == 64
    pending = cases[0]
    pending["metadata"]["review_status"] = "candidate"
    path = tmp_path / "candidates.jsonl"
    path.write_text(json.dumps(pending) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs human approval"):
        runner.load_cases(path)


def test_cooldown_uses_reply_time_and_retry_after_without_replaying_extra_requests(monkeypatch):
    now = [0]
    arrivals = []
    bodies = []

    def wait(seconds):
        now[0] += seconds

    def respond(request):
        arrivals.append(now[0])
        bodies.append(json.loads(request.content))
        now[0] += 2
        if len(arrivals) == 1:
            return httpx.Response(429, headers={"Retry-After": "17"})
        return reply("a" * 32)

    monkeypatch.setattr(runner, "monotonic", lambda: now[0])
    monkeypatch.setattr(runner, "sleep", wait)
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(respond)) as client:
        chat = runner.ChatClient(client, interval=8, retries=2)
        chat.send("first", None)
        chat.send("second", "a" * 32)

    assert arrivals == [0, 19, 29]
    assert bodies == [{"message": "first"}, {"message": "first"}, {"message": "second", "session_id": "a" * 32}]


def test_transport_timeout_is_never_retried(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    monkeypatch.setattr(runner, "sleep", lambda _: None)
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(runner.RunError, match="ReadTimeout"):
            runner.ChatClient(client, interval=8, retries=2).send("hello", None)
    assert len(calls) == 1


def test_tool_grades_match_manifest_and_require_explicit_empty_note_results():
    expected = [{"name": "search_sample_notes", "status": "ok", "args": {"query": "q"}, "result_ids": []}]
    call = {"name": "search_sample_notes", "status": "ok", "args": {"query": "q"}}
    assert runner.score_tools(None, [call]) == "unchecked"
    assert runner.score_tools([], [call]) == "fail"
    assert runner.score_tools(expected, [call]) == "fail"
    assert runner.score_tools(expected, [{**call, "result": {"results": []}}]) == "pass"
    assert runner.score_tools(expected, [{**call, "result": {"results": [{"id": "n2"}]}}]) == "fail"
    wanted = [{**expected[0], "result_ids": ["n1", "n8"]}]
    reversed_ids = [{**call, "result": {"results": [{"id": "n8"}, {"id": "n1"}]}}]
    assert runner.score_tools(wanted, reversed_ids) == "fail"


def test_runner_can_resume_failed_case_in_a_fresh_chat_and_preserves_completed_results(monkeypatch, tmp_path):
    approved, _ = runner.load_cases(runner.DATASET)
    first = next(case for case in approved if case["id"] == "project-001")
    follow_up = next(case for case in approved if len(case["input"]["turns"]) == 2)
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text("\n".join(json.dumps(case) for case in (first, follow_up)), encoding="utf-8")
    (tmp_path / "source-snapshot.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "reports" / "run.json"
    posts = []

    def respond(request):
        if request.url.path == "/api/settings":
            return httpx.Response(200, json={"provider": "nim", "key_configured": True, "model": "test-model"})
        posts.append(json.loads(request.content))
        index = len(posts)
        if index == 3:
            return httpx.Response(503, json={"error": {"code": "provider_unavailable"}})
        if index == 1:
            return reply("a" * 32, activity=first["expected_output"]["tool_calls"])
        return reply("b" * 32 if index == 2 else "c" * 32)

    real_client = httpx.Client
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(respond)
    ))
    monkeypatch.setattr(runner, "sleep", lambda _: None)
    args = ["run.py", "--dataset", str(dataset), "--out", str(output), "--live", "--retries", "0"]
    monkeypatch.setattr("sys.argv", args)
    assert runner.main() == 2
    report = runner.load_report(output)
    report["results"][first["id"]]["answer_grade"] = "pass"
    runner.save_report(output, report)
    monkeypatch.setattr("sys.argv", [*args, "--resume"])
    assert runner.main() == 0
    assert ["session_id" in post for post in posts] == [False, False, True, False, True]
    assert posts[2]["session_id"] == "b" * 32 and posts[4]["session_id"] == "c" * 32
    report = runner.load_report(output)
    assert report["results"][first["id"]]["answer_grade"] == "pass"
    assert report["results"][follow_up["id"]]["previous_errors"] == ["Chat failed with HTTP 503 (provider_unavailable)."]
    monkeypatch.setattr("sys.argv", ["run.py", "--summary", "--out", str(output)])
    assert runner.main() == 0
    assert len(posts) == 5
