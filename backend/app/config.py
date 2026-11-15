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


class ModelInfo(BaseModel):
    id: str
    label: str
    input_per_mtok_usd: float = Field(ge=0)
    output_per_mtok_usd: float = Field(ge=0)
    tool_capable: bool = True
    provider: Literal["openrouter", "nim"] = "openrouter"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # The key is read from OPENROUTER_API_KEY (the variable langchain-openrouter also uses).
    openrouter_api_key: SecretStr | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    nvidia_api_key: SecretStr | None = Field(default=None, validation_alias="NVIDIA_API_KEY")
    langsmith_api_key: SecretStr | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")

    provider: Literal["openrouter", "nim", "demo"] = Field(default="openrouter", validation_alias="CHOTU_PROVIDER")
    default_model: str = Field(default="openai/gpt-5-nano", validation_alias="CHOTU_DEFAULT_MODEL")
    monthly_budget_inr: float = Field(default=300.0, ge=0, validation_alias="CHOTU_MONTHLY_BUDGET_INR")
    usd_to_inr: float = Field(default=90.0, gt=0, validation_alias="CHOTU_USD_TO_INR")
    db_path: Path = Field(default=Path("data/chotubot.sqlite3"), validation_alias="CHOTU_DB_PATH")
    models_config_path: Path = Field(default=Path("config/models.json"), validation_alias="CHOTU_MODELS_CONFIG")
    fixtures_dir: Path = Field(default=Path("evals/fixtures"), validation_alias="CHOTU_FIXTURES_DIR")
    langsmith_enabled: bool = Field(default=False, validation_alias="CHOTU_LANGSMITH_ENABLED")
    timezone: str = "Asia/Kolkata"
    request_timeout_s: int = 60
    max_tool_rounds: int = 4
    history_message_limit: int = 20

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def active_key(self) -> SecretStr | None:
        return self.nvidia_api_key if self.provider == "nim" else self.openrouter_api_key

    @property
    def key_env_var(self) -> str:
        return "NVIDIA_API_KEY" if self.provider == "nim" else "OPENROUTER_API_KEY"

    @property
    def model_provider(self) -> str:
        """Which allowlist section applies. The offline demo provider reuses the OpenRouter list for display."""
        return "nim" if self.provider == "nim" else "openrouter"

    @property
    def key_configured(self) -> bool:
        k = self.active_key
        return bool(k and k.get_secret_value().strip())

    def secret_values(self) -> list[str]:
        out: list[str] = []
        for s in (self.openrouter_api_key, self.nvidia_api_key, self.langsmith_api_key):
            if s and s.get_secret_value().strip():
                out.append(s.get_secret_value())
        return out

    def load_models(self) -> list[ModelInfo]:
        data = json.loads(self.resolve(self.models_config_path).read_text(encoding="utf-8"))
        return [ModelInfo(**m) for m in data["models"]]


@lru_cache
def get_settings() -> Settings:
    return Settings()
