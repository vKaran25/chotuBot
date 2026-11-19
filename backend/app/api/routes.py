"""HTTP capabilities: health, chat, sessions, settings, usage (P1-REQ-1, -2, -6, -7, -9, -11)."""
from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.agent.graph import ChatError
from app.api.schemas import SESSION_ID_PATTERN, ChatRequest, CreateSessionRequest, SettingsUpdate
from app.config import VERSION
from app.models.provider import allowed_models
from app.agent.service import MODEL_KEY

import re

router = APIRouter(prefix="/api")
_SID = re.compile(SESSION_ID_PATTERN)


def _svc(request: Request):
    return request.app.state.services


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    return {"status": "ok", "version": VERSION}


def _sse(events) -> Iterator[str]:
    for ev in events:
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@router.post("/chat", response_model=None)
def chat(body: ChatRequest, request: Request) -> dict[str, Any] | StreamingResponse:
    svc = _svc(request)
    # Streaming (owner-approved 2026-11-19) only when the client asks for it; otherwise the JSON contract is unchanged.
    if "text/event-stream" in request.headers.get("accept", ""):
        events = (svc.documents.chat_stream(body.message, body.session_id, body.workspace_id) if body.use_documents
                  else svc.chat.chat_stream(body.message, body.session_id))
        return StreamingResponse(_sse(events), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    if body.use_documents:  # Phase 2 document question (docs/specs/document-chat.md)
        return svc.documents.chat(body.message, body.session_id, body.workspace_id)
    return svc.chat.chat(body.message, body.session_id)


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


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, request: Request) -> Response:
    if not (_SID.match(session_id) and _svc(request).storage.delete_session(session_id)):
        raise ChatError(404, "session_not_found", "No such conversation.")
    return Response(status_code=204)


def _settings_view(svc) -> dict[str, Any]:
    st = svc.settings
    return {
        "provider": st.provider,
        "key_configured": st.key_configured,  # presence only; the key itself is never returned
        "key_env_var": st.key_env_var,
        "model": svc.chat.effective_model(),
        "models": [m.model_dump() for m in allowed_models(st)],
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
    return _settings_view(svc)


@router.get("/usage")
def usage(request: Request) -> dict[str, Any]:
    return _svc(request).storage.usage_overview(50)
