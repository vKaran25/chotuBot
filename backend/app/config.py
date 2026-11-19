"""Application configuration. Values come from environment variables / a git-ignored .env."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.0"
KEY_ENV_VAR = "NVIDIA_API_KEY"


class ModelInfo(BaseModel):
    id: str
    label: str
    tool_capable: bool = True
    category: Literal["fast", "thinking", "flagship"] = "fast"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    nvidia_api_key: SecretStr | None = Field(default=None, validation_alias="NVIDIA_API_KEY")
    langsmith_api_key: SecretStr | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")

    # "nim" = NVIDIA NIM (real). "demo" = offline rule-based model for trying the UI without a key.
    provider: Literal["nim", "demo"] = Field(default="nim", validation_alias="CHOTU_PROVIDER")
    default_model: str = Field(default="nvidia/nemotron-3.5-lightning-30b-a3b", validation_alias="CHOTU_DEFAULT_MODEL")
    db_path: Path = Field(default=Path("data/chotubot.sqlite3"), validation_alias="CHOTU_DB_PATH")
    models_config_path: Path = Field(default=Path("config/models.json"), validation_alias="CHOTU_MODELS_CONFIG")
    fixtures_dir: Path = Field(default=Path("evals/fixtures"), validation_alias="CHOTU_FIXTURES_DIR")
    langsmith_enabled: bool = Field(default=False, validation_alias="CHOTU_LANGSMITH_ENABLED")
    port: int = Field(default=8765, ge=1024, le=65535, validation_alias="CHOTU_PORT")
    max_tool_rounds: int = 4
    history_message_limit: int = 20

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def key_env_var(self) -> str:
        return KEY_ENV_VAR

    @property
    def key_configured(self) -> bool:
        k = self.nvidia_api_key
        return bool(k and k.get_secret_value().strip())

    def secret_values(self) -> list[str]:
        return [s.get_secret_value() for s in (self.nvidia_api_key, self.langsmith_api_key)
                if s and s.get_secret_value().strip()]

    def load_models(self) -> list[ModelInfo]:
        data = json.loads(self.resolve(self.models_config_path).read_text(encoding="utf-8"))
        return [ModelInfo(**m) for m in data["models"]]


@lru_cache
def get_settings() -> Settings:
    return Settings()
