"""Token and cost accounting for one agent request (P1-REQ-11)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from langchain_core.messages import AIMessage

from app.config import ModelInfo


@dataclass
class UsageSummary:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: float
    cost_source: str  # provider | estimated | mixed | unavailable


def summarize(ai_messages: Iterable[AIMessage], model: ModelInfo | None) -> UsageSummary:
    n_in = n_out = 0
    seen_tokens = False
    provider_cost = estimated_cost = 0.0
    n_provider = n_estimated = n_unknown = 0
    for m in ai_messages:
        um = m.usage_metadata or {}
        tin, tout = um.get("input_tokens"), um.get("output_tokens")
        if tin is not None or tout is not None:
            seen_tokens = True
            n_in += tin or 0
            n_out += tout or 0
        reported = (m.response_metadata or {}).get("cost")
        if isinstance(reported, (int, float)) and reported >= 0:
            provider_cost += float(reported)
            n_provider += 1
        elif model is not None and (tin is not None or tout is not None):
            estimated_cost += ((tin or 0) * model.input_per_mtok_usd + (tout or 0) * model.output_per_mtok_usd) / 1e6
            n_estimated += 1
        else:
            n_unknown += 1
    if n_provider and not n_estimated and not n_unknown:
        source = "provider"
    elif n_estimated and not n_provider and not n_unknown:
        source = "estimated"
    elif n_provider or n_estimated:
        source = "mixed"
    else:
        source = "unavailable"
    return UsageSummary(
        input_tokens=n_in if seen_tokens else None,
        output_tokens=n_out if seen_tokens else None,
        total_tokens=(n_in + n_out) if seen_tokens else None,
        cost_usd=provider_cost + estimated_cost,
        cost_source=source,
    )
