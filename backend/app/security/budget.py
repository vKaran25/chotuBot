"""Monthly budget guard (P1-REQ-10).

Estimates only: cost is what OpenRouter reports (or a token-based estimate) converted with a fixed
USD->INR rate. It is NOT a guarantee of provider billing totals. A request already in flight when the
budget is reached finishes and is recorded.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.storage import Storage

BUDGET_KEY = "monthly_budget_inr"
MODEL_KEY = "model"


class BudgetExceeded(Exception):
    def __init__(self, spent: float, limit: float):
        super().__init__(f"Monthly budget reached: spent about INR {spent:.2f} of INR {limit:.2f}")
        self.spent, self.limit = spent, limit


def month_key(now: datetime, tz: str) -> str:
    return now.astimezone(ZoneInfo(tz)).strftime("%Y-%m")


class BudgetGuard:
    def __init__(self, storage: Storage, settings: Settings, clock: Callable[[], datetime] | None = None):
        self.storage, self.settings = storage, settings
        self.clock = clock or (lambda: datetime.now(ZoneInfo("UTC")))

    def current_month(self) -> str:
        return month_key(self.clock(), self.settings.timezone)

    def limit_inr(self) -> float:
        raw = self.storage.get_setting(BUDGET_KEY)
        return float(raw) if raw is not None else self.settings.monthly_budget_inr

    def spent_inr(self) -> float:
        return self.storage.month_spend_inr(self.current_month())

    def check(self) -> None:
        """Raise BudgetExceeded when no further paid request is allowed this month."""
        spent, limit = self.spent_inr(), self.limit_inr()
        if spent >= limit:
            raise BudgetExceeded(spent, limit)
