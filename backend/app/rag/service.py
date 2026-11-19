"""Document management and document chat orchestration (P2-REQ-2..13)."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

from app.agent.graph import ChatError
from app.config import Settings
from app.rag.answer import DocumentTurn
from app.rag.chunking import chunk_document
from app.rag.config import TOP_K, RagSettings
from app.rag.embeddings import Embedder, build_embedder
from app.rag.errors import DocumentError, not_found
from app.rag.extract import EXTENSIONS, clean_filename, file_type_for, validate_and_extract
from app.rag.index import IndexManager
from app.rag.retriever import FaissRetriever, Retriever
from app.security.documents import require_sharing_consent
from app.storage.db import new_id
from app.storage.documents import DocumentStore, DuplicateDocument, WorkspaceNameTaken

log = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, settings: Settings, rag: RagSettings, storage, chat, embedder: Embedder | None = None):
        self.settings, self.rag = settings, rag
        self.storage, self.chat_service = storage, chat
        self.root = rag.root(settings)
        self.files_dir = self.root / "files"
        self.store = DocumentStore(settings.resolve(settings.db_path))
        self.embedder = embedder or build_embedder(rag.embedder, rag.models_dir(settings))
        self.indexes = IndexManager(self.root / "indexes", self.embedder.dim, self.embedder.name)
        self.retriever: Retriever = FaissRetriever(self.store, self.indexes, self.embedder, self.rebuild_index)
        self._mutate = threading.Lock()  # one document mutation at a time (single owner)

    # ---- workspaces -----------------------------------------------------
    def workspace(self, ws_id: str) -> dict[str, Any]:
        ws = self.store.get_workspace(ws_id)
        if ws is None:
            raise not_found("workspace")
        return ws

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.store.list_workspaces()

    def create_workspace(self, name: str) -> dict[str, Any]:
        try:
            return self.store.create_workspace(name)
        except WorkspaceNameTaken:
            raise DocumentError(409, "workspace_name_taken", "A workspace with this name already exists.") from None

    def set_consent(self, ws_id: str, allowed: bool) -> dict[str, Any]:
        ws = self.store.set_consent(ws_id, allowed)
        if ws is None:
            raise not_found("workspace")
        return ws

    # ---- documents ------------------------------------------------------
    def _path(self, ws: str, doc_id: str, file_type: str) -> Path:
        return self.files_dir / ws / f"{doc_id}{EXTENSIONS[file_type]}"

    def _embed_chunks(self, doc_id: str, ws: str, extracted) -> list[dict[str, Any]]:
        chunks = chunk_document(doc_id, extracted)
        vectors = self.embedder.embed_documents([c.text for c in chunks])
        return [{"id": c.id, "document_id": doc_id, "workspace_id": ws, "ordinal": c.ordinal, "text": c.text,
                 "location": c.location, "start_offset": c.start, "end_offset": c.end, "vector": v}
                for c, v in zip(chunks, vectors)]

    def rebuild_index(self, ws: str) -> None:
        ids, vecs = self.store.workspace_vectors(ws, self.embedder.name, self.embedder.dim)
        self.indexes.rebuild(ws, ids, vecs)

    def _safe_rebuild(self, ws: str) -> None:
        try:
            self.rebuild_index(ws)
        except Exception:  # the FAISS file is a cache; the next search rebuilds it from SQLite
            log.exception("index rebuild failed")
            self.indexes.drop(ws)

    def upload(self, ws_id: str, raw_name: str | None, data: bytes) -> dict[str, Any]:
        self.workspace(ws_id)
        filename = clean_filename(raw_name)
        file_type_for(filename)
        extracted = validate_and_extract(filename, data)
        sha = hashlib.sha256(data).hexdigest()
        with self._mutate:
            dup = self.store.find_duplicate(ws_id, sha)
            if dup:
                raise self._dup(dup)
            doc_id = new_id()
            chunks = self._embed_chunks(doc_id, ws_id, extracted)
            path = self._path(ws_id, doc_id, extracted.file_type)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            try:
                doc = self.store.insert_document(
                    {"id": doc_id, "workspace_id": ws_id, "filename": filename, "file_type": extracted.file_type,
                     "size_bytes": len(data), "sha256": sha, "page_count": extracted.page_count,
                     "embedder": self.embedder.name},
                    chunks,
                )
            except DuplicateDocument as e:
                path.unlink(missing_ok=True)
                raise self._dup(e.document_id) from None
            except Exception:
                path.unlink(missing_ok=True)
                raise
            self._safe_rebuild(ws_id)
        return DocumentStore.public(doc)

    @staticmethod
    def _dup(doc_id: str) -> DocumentError:
        return DocumentError(409, "duplicate_document", "This file is already in the workspace.", document_id=doc_id)

    def _owned(self, ws_id: str, doc_id: str) -> dict[str, Any]:
        doc = self.store.get_document(doc_id)
        if doc is None or doc["workspace_id"] != ws_id:  # never reveal another workspace's documents
            raise not_found("document")
        return doc

    def list_documents(self, ws_id: str) -> list[dict[str, Any]]:
        self.workspace(ws_id)
        return [DocumentStore.public(d) for d in self.store.list_documents(ws_id)]

    def reindex(self, ws_id: str, doc_id: str) -> dict[str, Any]:
        self.workspace(ws_id)
        with self._mutate:
            doc = self._owned(ws_id, doc_id)
            path = self._path(ws_id, doc_id, doc["file_type"])
            try:
                data = path.read_bytes()
            except OSError:
                raise DocumentError(409, "source_file_missing",
                                    "The stored original is missing; delete and upload the document again.") from None
            extracted = validate_and_extract(doc["filename"], data)
            chunks = self._embed_chunks(doc_id, ws_id, extracted)
            self.store.replace_chunks(doc_id, chunks, page_count=extracted.page_count, embedder=self.embedder.name)
            self._safe_rebuild(ws_id)
            return DocumentStore.public(self.store.get_document(doc_id))

    def delete(self, ws_id: str, doc_id: str) -> None:
        self.workspace(ws_id)
        with self._mutate:
            doc = self._owned(ws_id, doc_id)
            self.store.delete_document(doc_id)
            self._path(ws_id, doc_id, doc["file_type"]).unlink(missing_ok=True)
            self._safe_rebuild(ws_id)

    # ---- citations ------------------------------------------------------
    def citation(self, cit_id: str) -> dict[str, Any]:
        c = self.store.get_citation(cit_id)
        if c is None:
            raise not_found("citation")
        return c

    def session_citations(self, session_id: str) -> dict[str, Any]:
        if not self.storage.session_exists(session_id):
            raise ChatError(404, "session_not_found", "No such conversation.")
        return {"citations": self.store.session_citations(session_id)}

    # ---- document chat --------------------------------------------------
    def chat(self, message: str, session_id: str | None, ws_id: str) -> dict[str, Any]:
        ws = self.workspace(ws_id)
        require_sharing_consent(self.settings, ws)  # before anything could reach the cloud model
        if session_id is not None and not self.storage.session_exists(session_id):
            raise ChatError(404, "session_not_found", "No such conversation.")
        hits = [h for h in self.retriever.retrieve(ws_id, message, TOP_K) if h.score >= self.retriever.min_score]
        turn = DocumentTurn(hits, self.settings.provider, message)
        result = self.chat_service.chat(message, session_id, context=turn)
        items = turn.citation_items()
        result["citations"] = (
            self.store.add_citations(result["session_id"], result["request_id"], ws_id, items) if items else []
        )
        return result
