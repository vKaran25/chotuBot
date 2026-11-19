"""Document workspace/consent guard (P2-REQ-9, P2-REQ-13). Does not touch the Phase 1 tool policy."""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.rag.errors import DocumentError


def provider_shares_data(settings: Settings) -> bool:
    """True when chat content leaves the machine (NVIDIA NIM). The offline `demo` provider does not."""
    return settings.provider != "demo"


def require_sharing_consent(settings: Settings, workspace: dict[str, Any]) -> None:
    if provider_shares_data(settings) and not workspace["sharing"]["allowed"]:
        raise DocumentError(
            409,
            "document_consent_required",
            "Sharing document excerpts with the cloud model is off for this workspace. "
            "Turn on “Allow sharing” to ask questions about your documents.",
        )
