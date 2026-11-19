"""Phase 2 RAG settings and fixed parameters (owner-approved, ADR 0003).

Kept separate from `app.config.Settings` so the Phase 1 settings object is unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import REPO_ROOT, Settings

INDEX_VERSION = "v1"
CHUNK_SIZE = 1000  # characters
CHUNK_OVERLAP = 150  # characters
TOP_K = 5
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-d, English, via fastembed (ONNX, CPU)
EMBED_DIM = 384
# Uncalibrated placeholder cutoffs (cosine similarity). Phase 3 evaluations calibrate these.
FASTEMBED_MIN_SCORE = 0.60
HASH_MIN_SCORE = 0.15
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_FILE_BYTES + 512 * 1024  # multipart framing headroom
MAX_FILENAME_CHARS = 200
UPLOAD_PATH = "/api/documents"


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # Where uploaded originals and FAISS indexes live. Default: next to the SQLite file, "<db stem>.rag/".
    rag_dir: Path | None = Field(default=None, validation_alias="CHOTU_RAG_DIR")
    # "fastembed" = real local model (default). "hash" = deterministic word-hashing embedder for tests only.
    embedder: Literal["fastembed", "hash"] = Field(default="fastembed", validation_alias="CHOTU_EMBEDDER")
    models_cache_dir: Path = Field(default=Path("data/models"), validation_alias="CHOTU_MODELS_CACHE")

    def root(self, settings: Settings) -> Path:
        if self.rag_dir is not None:
            return settings.resolve(self.rag_dir)
        db = settings.resolve(settings.db_path)
        return db.parent / f"{db.stem}.rag"

    def models_dir(self, settings: Settings) -> Path:
        return settings.resolve(self.models_cache_dir)
