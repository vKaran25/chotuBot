"""P1-T15 / P1-T17: deterministic read-only tools and the authorization boundary."""
from __future__ import annotations

import pytest

from app.security.tool_policy import ALLOWED_TOOLS, ToolDenied, authorize_tool


def test_exactly_two_allowed_tools():
    assert ALLOWED_TOOLS == {"get_project_status", "search_sample_notes"}


def test_project_status_known_project_is_deterministic(registry):
    a = registry.execute("c1", "get_project_status", {"project": "atlas"})
    b = registry.execute("c2", "get_project_status", {"project": "ATLAS "})
    assert a["status"] == "ok"
    assert a["result"] == b["result"]
    assert a["result"]["project"]["status"] == "on_track"
    assert a["result"]["project"]["progress_percent"] == 72


def test_project_status_without_project_lists_all(registry):
    r = registry.execute("c1", "get_project_status", {})
    assert r["status"] == "ok"
    assert [p["name"] for p in r["result"]["projects"]] == ["atlas", "beacon", "cedar"]


def test_project_status_unknown_project_is_tool_error_not_exception(registry):
    r = registry.execute("c1", "get_project_status", {"project": "zeta"})
    assert r["status"] == "error"
    assert "Unknown project" in r["error"]
    assert r["result"] is None


def test_search_notes_returns_ranked_fixture_results(registry):
    r = registry.execute("c1", "search_sample_notes", {"query": "beacon auth"})
    assert r["status"] == "ok"
    ids = [n["id"] for n in r["result"]["results"]]
    assert ids[0] == "n2"  # matches both terms
    assert r["result"]["count"] == len(ids) <= 5
    assert registry.execute("c2", "search_sample_notes", {"query": "beacon auth"})["result"] == r["result"]


def test_search_notes_no_match_is_empty_not_error(registry):
    r = registry.execute("c1", "search_sample_notes", {"query": "zzzzqqq"})
    assert r["status"] == "ok" and r["result"]["count"] == 0


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "x" * 201}, {"query": "ok", "path": "/etc/passwd"}, {"query": 5}])
def test_search_notes_rejects_invalid_args(registry, args):
    r = registry.execute("c1", "search_sample_notes", args)
    assert r["status"] == "error" and "Invalid arguments" in r["error"]


def test_project_status_rejects_extra_args(registry):
    r = registry.execute("c1", "get_project_status", {"project": "atlas", "write": True})
    assert r["status"] == "error"


def test_results_cannot_mutate_fixtures(registry):
    r = registry.execute("c1", "get_project_status", {"project": "atlas"})
    r["result"]["project"]["status"] = "hacked"
    again = registry.execute("c2", "get_project_status", {"project": "atlas"})
    assert again["result"]["project"]["status"] == "on_track"


@pytest.mark.parametrize(
    "name",
    ["delete_file", "write_note", "send_email", "shell", "get_project_status ", "GET_PROJECT_STATUS", "", None, 5, "../x"],
)
def test_unregistered_or_malformed_tool_names_are_denied(registry, name):
    r = registry.execute("c1", name, {"anything": 1})
    assert r["status"] == "denied"
    assert r["result"] is None


def test_authorize_tool_raises_for_unknown():
    with pytest.raises(ToolDenied):
        authorize_tool("rm")
    assert authorize_tool("get_project_status") == "get_project_status"


def test_only_two_tools_exposed_to_model(registry):
    assert {t.name for t in registry.langchain_tools()} == ALLOWED_TOOLS


def test_injection_text_in_note_is_data_only(registry):
    """The fixture note contains an injected instruction. Returning it must not execute anything."""
    r = registry.execute("c1", "search_sample_notes", {"query": "ignore previous instructions"})
    assert r["status"] == "ok"
    assert any("delete_file" in n["body"] for n in r["result"]["results"])  # returned as text only
