"""FastAPI application factory (local, single-owner)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.agent.graph import ChatError
from app.agent.service import ChatService, ModelFactory
from app.api.documents import router as documents_router
from app.api.routes import router
from app.config import REPO_ROOT, VERSION, Settings, get_settings
from app.models.provider import build_chat_model
from app.observability.tracing import configure_tracing
from app.rag.config import MAX_UPLOAD_REQUEST_BYTES, UPLOAD_PATH, RagSettings
from app.rag.errors import DocumentError
from app.rag.service import DocumentService
from app.security.redaction import install_log_scrubber
from app.storage import Storage
from app.tools.registry import SampleToolRegistry

log = logging.getLogger("app")
MAX_BODY_BYTES = 64 * 1024
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


@dataclass
class Services:
    settings: Settings
    storage: Storage
    registry: SampleToolRegistry
    chat: ChatService
    documents: DocumentService | None = None  # Phase 2


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}})


def create_app(
    settings: Settings | None = None,
    model_factory: ModelFactory | None = None,
    callbacks: list | None = None,
    rag_settings: RagSettings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    install_log_scrubber(settings.secret_values())
    storage = Storage(settings.resolve(settings.db_path))
    registry = SampleToolRegistry(settings.resolve(settings.fixtures_dir))
    if callbacks is None:
        callbacks = configure_tracing(settings)
    chat = ChatService(settings, storage, registry, model_factory or build_chat_model, callbacks)
    documents = DocumentService(settings, rag_settings or RagSettings(), storage, chat)

    app = FastAPI(title="chotuBot", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = Services(settings, storage, registry, chat, documents)
    # Defends against DNS-rebinding: only loopback host names are served.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.middleware("http")
    async def guard_mutations(request: Request, call_next):
        # CSRF defence for a local API that spends money: a web page the owner visits must not be able to POST here.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and urlparse(origin).hostname not in ALLOWED_HOSTS
            ):
                return _error(403, "forbidden_origin", "Cross-site requests are not allowed.")
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            clen = request.headers.get("content-length", "")
            has_body = (clen.isdigit() and int(clen) > 0) or "transfer-encoding" in request.headers
            # Phase 2 (owner-approved): multipart only for the document upload route, with a known length.
            upload = request.method == "POST" and request.url.path == UPLOAD_PATH and ctype == "multipart/form-data"
            if has_body and ctype != "application/json" and not upload:
                return _error(415, "unsupported_media_type", "Send JSON with Content-Type: application/json.")
            if upload and not clen.isdigit():
                return _error(411, "length_required", "Uploads need a Content-Length header.")
            if upload and int(clen) > MAX_UPLOAD_REQUEST_BYTES:
                return _error(413, "payload_too_large", "Request body is too large.")
            if upload:
                return await call_next(request)
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
            return _error(413, "payload_too_large", "Request body is too large.")
        return await call_next(request)

    @app.exception_handler(ChatError)
    async def _chat_error(_: Request, exc: ChatError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(DocumentError)
    async def _document_error(_: Request, exc: DocumentError):
        return _error(exc.status_code, exc.code, exc.message, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Report where and why, never the submitted values.
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or "body", "reason": e["msg"]} for e in exc.errors()]
        return _error(422, "invalid_request", "The request is invalid.", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _error(exc.status_code, code, str(exc.detail) if exc.status_code != 404 else "Not found.")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.error("unhandled error: %s", type(exc).__name__)
        return _error(500, "internal_error", "An internal error occurred.")

    app.include_router(router)
    app.include_router(documents_router)  # Phase 2: workspaces, documents, citations

    dist = REPO_ROOT / "frontend" / "dist"
    if dist.is_dir():  # built UI served by the same local process; otherwise use the Vite dev server
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app
