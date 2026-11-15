"""Tool authorization boundary (P1-REQ-14).

The allowlist is fixed in code. It is enforced at execution time, so it holds no matter what the
model asks for, what a prompt says, or what a tool result contains.
"""
from __future__ import annotations

ALLOWED_TOOLS: frozenset[str] = frozenset({"get_project_status", "search_sample_notes"})


class ToolDenied(Exception):
    """Raised when a tool call names anything outside the Phase 1 read-only sample tools."""


def authorize_tool(name: object) -> str:
    if not isinstance(name, str) or name not in ALLOWED_TOOLS:
        raise ToolDenied(f"Tool {str(name)[:60]!r} is not permitted. Allowed tools: {sorted(ALLOWED_TOOLS)}")
    return name
