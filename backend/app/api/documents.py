"""Phase 2 HTTP capabilities: workspaces, consent, documents, citations (docs/specs/workspaces.md, documents.md,
document-chat.md). Additive router; Phase 1 routes are untouched."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.agent.graph import ChatError
from app.api.schemas import SESSION_ID_PATTERN, WORKSPACE_ID_PATTERN
from app.rag.config import MAX_FILE_BYTES
from app.rag.errors import DocumentError, not_found
from app.security.documents import provider_shares_data

_WS = re.compile(WORKSPACE_ID_PATTERN)
_HEX = re.compile(r"^[0-9a-f]{32}$")
_SID = re.compile(SESSION_ID_PATTERN)

router = APIRouter(prefix="/api")


class CreateWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)


class ConsentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: StrictBool


def _docs(request: Request):
    return request.app.state.services.documents


def _ws(ws_id: str) -> str:
    if not _WS.match(ws_id):
        raise not_found("workspace")
    return ws_id


def _doc_id(doc_id: str) -> str:
    if not _HEX.match(doc_id):
        raise not_found("document")
    return doc_id


# ---- workspaces ----------------------------------------------------------
@router.get("/workspaces")
def list_workspaces(request: Request) -> dict[str, Any]:
    svc = _docs(request)
    return {"workspaces": svc.list_workspaces(), "consent_required": provider_shares_data(svc.settings)}


@router.post("/workspaces", status_code=201)
def create_workspace(body: CreateWorkspace, request: Request) -> dict[str, Any]:
    return _docs(request).create_workspace(body.name)


@router.put("/workspaces/{workspace_id}/consent")
def set_consent(workspace_id: str, body: ConsentUpdate, request: Request) -> dict[str, Any]:
    return _docs(request).set_consent(_ws(workspace_id), body.allowed)


# ---- documents -----------------------------------------------------------
@router.post("/documents", status_code=201)
def upload_document(
    request: Request,
    file: list[UploadFile] = File(...),
    workspace_id: str = Form("default", pattern=WORKSPACE_ID_PATTERN),
) -> dict[str, Any]:
    if len(file) != 1:
        raise DocumentError(422, "invalid_request", "The request is invalid.",
                            details=[{"field": "file", "reason": "Exactly one file is required."}])
    up = file[0]
    data = up.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(413, "file_too_large", "The file is larger than 10 MB.")
    return _docs(request).upload(workspace_id, up.filename, data)


@router.get("/documents")
def list_documents(request: Request, workspace_id: str = "default") -> dict[str, Any]:
    return {"documents": _docs(request).list_documents(_ws(workspace_id))}


@router.post("/documents/{document_id}/reindex")
def reindex_document(document_id: str, request: Request, workspace_id: str = "default") -> dict[str, Any]:
    return _docs(request).reindex(_ws(workspace_id), _doc_id(document_id))


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: str, request: Request, workspace_id: str = "default") -> Response:
    _docs(request).delete(_ws(workspace_id), _doc_id(document_id))
    return Response(status_code=204)


# ---- citations -----------------------------------------------------------
@router.get("/citations/{citation_id}")
def get_citation(citation_id: str, request: Request) -> dict[str, Any]:
    if not _HEX.match(citation_id):
        raise not_found("citation")
    return _docs(request).citation(citation_id)


@router.get("/sessions/{session_id}/citations")
def session_citations(session_id: str, request: Request) -> dict[str, Any]:
    if not _SID.match(session_id):
        raise ChatError(404, "session_not_found", "No such conversation.")
    return _docs(request).session_citations(session_id)
