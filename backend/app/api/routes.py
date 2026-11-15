"""HTTP capabilities: health, chat, sessions, settings, usage (P1-REQ-1, -2, -6, -7, -9, -11)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.agent.graph import ChatError
from app.api.schemas import SESSION_ID_PATTERN, ChatRequest, CreateSessionRequest, SettingsUpdate
from app.config import VERSION
from app.models.provider import allowed_models
from app.security.budget import BUDGET_KEY, MODEL_KEY

import re

router = APIRouter(prefix="/api")
_SID = re.compile(SESSION_ID_PATTERN)

DISCLAIMER = (
    "Costs are local estimates (OpenRouter-reported cost or a token-based estimate, converted at a fixed "
    "USD->INR rate). They are not a guarantee of provider billing totals."
)


def _svc(request: Request):
    return request.app.state.services


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    return {"status": "ok", "version": VERSION}


@router.post("/chat")
def chat(body: ChatRequest, request: Request) -> dict[str, Any]:
    return _svc(request).chat.chat(body.message, body.session_id)


@router.get("/sessions")
def list_sessions(request: Request) -> dict[str, Any]:
    return {"sessions": _svc(request).storage.list_sessions()}


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionRequest, request: Request) -> dict[str, Any]:
    return _svc(request).storage.create_session(body.title or "New conversation")


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    s = _svc(request).storage.get_session(session_id) if _SID.match(session_id) else None
    if s is None:
        raise ChatError(404, "session_not_found", "No such conversation.")
    return s


def _settings_view(svc) -> dict[str, Any]:
    st = svc.settings
    return {
        "provider": st.provider,
        "key_configured": st.key_configured,  # presence only; the key itself is never returned
        "key_env_var": st.key_env_var,
        "model": svc.chat.effective_model(),
        "models": [m.model_dump() for m in allowed_models(st)],
        "monthly_budget_inr": svc.chat.guard.limit_inr(),
        "usd_to_inr": st.usd_to_inr,
        "tracing_enabled": bool(svc.chat.callbacks),
    }


@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    return _settings_view(_svc(request))


@router.put("/settings")
def put_settings(body: SettingsUpdate, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    if body.model is not None:
        if body.model not in {m.id for m in allowed_models(svc.settings)}:
            raise ChatError(422, "model_not_supported", "That model is not in the allowed tool-capable model list.")
        svc.storage.set_setting(MODEL_KEY, body.model)
    if body.monthly_budget_inr is not None:
        svc.storage.set_setting(BUDGET_KEY, repr(body.monthly_budget_inr))
    return _settings_view(svc)


@router.get("/usage")
def usage(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    guard = svc.chat.guard
    spent, limit = guard.spent_inr(), guard.limit_inr()
    return {
        "month": guard.current_month(),
        "spent_inr": spent,
        "budget_inr": limit,
        "remaining_inr": max(limit - spent, 0.0),
        "usd_to_inr": svc.settings.usd_to_inr,
        "disclaimer": DISCLAIMER,
        "records": svc.storage.list_usage(50),
    }
