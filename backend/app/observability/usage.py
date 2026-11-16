"""Token accounting for one agent request."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from langchain_core.messages import AIMessage


@dataclass
class UsageSummary:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


def summarize(ai_messages: Iterable[AIMessage]) -> UsageSummary:
    n_in = n_out = 0
    seen = False
    for m in ai_messages:
        um = m.usage_metadata or {}
        tin, tout = um.get("input_tokens"), um.get("output_tokens")
        if tin is not None or tout is not None:
            seen = True
            n_in += tin or 0
            n_out += tout or 0
    return UsageSummary(n_in if seen else None, n_out if seen else None, (n_in + n_out) if seen else None)
