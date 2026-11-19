"""Document-feature errors. Subclass of the Phase 1 `ChatError`, so they use the same JSON error shape."""
from __future__ import annotations

from typing import Any

from app.agent.graph import ChatError


class DocumentError(ChatError):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any):
        super().__init__(status_code, code, message)
        self.extra = extra


def not_found(kind: str) -> DocumentError:
    return DocumentError(404, f"{kind}_not_found", f"No such {kind.replace('_', ' ')}.")
