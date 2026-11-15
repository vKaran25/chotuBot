"""Request bodies for the approved Phase 1 API contract. Unknown fields are rejected."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

SESSION_ID_PATTERN = r"^[0-9a-f]{32}$"
MAX_MESSAGE_CHARS = 4000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=100)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    monthly_budget_inr: float | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _not_empty(self) -> "SettingsUpdate":
        if self.model is None and self.monthly_budget_inr is None:
            raise ValueError("provide at least one of: model, monthly_budget_inr")
        return self
