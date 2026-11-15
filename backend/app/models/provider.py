"""Chat model construction: OpenRouter BYOK (P1-REQ-4, P1-REQ-9) or, for testing, NVIDIA NIM (ADR 0002)."""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import ModelInfo, Settings
from app.models.fakes import DemoChatModel


class MissingApiKey(Exception):
    """No OpenRouter key is configured on the backend."""


class ModelNotAllowed(Exception):
    """The requested model is not in the owner's tool-capable allowlist."""


def allowed_models(settings: Settings) -> list[ModelInfo]:
    return [m for m in settings.load_models() if m.tool_capable and m.provider == settings.model_provider]


def find_model(settings: Settings, model_id: str) -> ModelInfo:
    for m in allowed_models(settings):
        if m.id == model_id:
            return m
    raise ModelNotAllowed(model_id)


def build_chat_model(settings: Settings, model_id: str) -> BaseChatModel:
    """Returns a chat model. Raises MissingApiKey before any network call when no key is set."""
    if settings.provider == "demo":
        return DemoChatModel()
    if not settings.key_configured:
        raise MissingApiKey(f"{settings.key_env_var} is not configured")
    find_model(settings, model_id)

    if settings.provider == "nim":
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        return ChatNVIDIA(
            model=model_id,
            api_key=settings.nvidia_api_key.get_secret_value(),
            temperature=0,
            usage_telemetry_enabled=False,  # send nothing to NVIDIA beyond the inference request itself
        )

    from langchain_openrouter import ChatOpenRouter

    return ChatOpenRouter(
        model=model_id,
        api_key=settings.openrouter_api_key,  # SecretStr; langchain-openrouter unwraps it internally
        temperature=0,
        timeout=settings.request_timeout_s * 1000,  # milliseconds
        max_retries=0,  # fail fast: a local chat should report an outage, not hang for minutes
    )
