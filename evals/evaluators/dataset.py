"""Versioned golden-case format (P1-T05). A case can record input, expected result, category,
dataset version and its manual-review record."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CATEGORY_TARGETS = {
    "basic_conversation": 8,
    "tool_selection": 10,
    "invalid_or_failure": 6,
    "unauthorized_or_injection": 6,
}
PENDING = "PENDING HUMAN REVIEW"
REVIEWED = "REVIEWED"


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["PENDING HUMAN REVIEW", "REVIEWED"] = PENDING
    reviewer: str | None = None
    reviewed_at: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _reviewed_needs_record(self) -> "Review":
        if self.status == REVIEWED and not (self.reviewer and self.reviewed_at):
            raise ValueError("a REVIEWED case must record reviewer and reviewed_at")
        return self


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_configured: bool = True


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str | None = None
    message_repeat: dict[str, Any] | None = None  # {"text": "a", "times": 4001} for over-length inputs
    session_id: str | None = None  # "unknown" => a well-formed id that does not exist

    def resolved_message(self) -> str:
        if self.message_repeat:
            return self.message_repeat["text"] * int(self.message_repeat["times"])
        return self.message if self.message is not None else ""


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    args: dict[str, Any]


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http_status: int = 200
    error_code: str | None = None
    tool_calls: list[ExpectedToolCall]  # permitted tool calls expected; [] means "no tool"
    tool_statuses: list[str] | None = None
    response_contains: list[str] = Field(default_factory=list)  # all must appear (case-insensitive)
    response_must_not_contain: list[str] = Field(default_factory=list)


class ExpectedFrozen(BaseModel):
    """Assertions that only make sense for the frozen (compromised-model) scenario."""

    model_config = ConfigDict(extra="forbid")
    denied_tool_calls: list[str] = Field(default_factory=list)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    category: Literal["basic_conversation", "tool_selection", "invalid_or_failure", "unauthorized_or_injection"]
    description: str
    setup: Setup = Field(default_factory=Setup)
    request: Request
    frozen_model: list[dict[str, Any]] = Field(default_factory=list)
    live_eligible: bool = True
    expected: Expected
    expected_frozen: ExpectedFrozen = Field(default_factory=ExpectedFrozen)
    review: Review = Field(default_factory=Review)


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str
    version: str
    description: str
    review_policy: str
    cases: list[Case]

    @model_validator(mode="after")
    def _unique_ids(self) -> "Dataset":
        ids = [c.id for c in self.cases]
        dup = [i for i, n in Counter(ids).items() if n > 1]
        if dup:
            raise ValueError(f"duplicate case ids: {dup}")
        return self

    def category_counts(self) -> dict[str, int]:
        return dict(Counter(c.category for c in self.cases))

    def distribution_matches_spec(self) -> bool:
        return self.category_counts() == CATEGORY_TARGETS


def load_dataset(path: Path) -> Dataset:
    return Dataset.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
