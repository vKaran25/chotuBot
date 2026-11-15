"""Optional LangSmith tracing. OFF by default; opt-in only via CHOTU_LANGSMITH_ENABLED (P1-REQ-12)."""
from __future__ import annotations

import logging
import os
from typing import Any

from app.config import Settings
from app.security.redaction import redact_for_trace

log = logging.getLogger(__name__)

_TRACING_ENV_VARS = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


def configure_tracing(settings: Settings) -> list[Any]:
    """Return LangChain callbacks to attach to each run.

    Global tracing env vars are forced off so a stray ambient `LANGSMITH_TRACING=true` can never send
    unredacted data. When the owner opts in, one tracer is returned whose client redacts every input,
    output and metadata field before it leaves the process.
    """
    for var in _TRACING_ENV_VARS:
        os.environ[var] = "false"
    if not settings.langsmith_enabled:
        return []
    if not (settings.langsmith_api_key and settings.langsmith_api_key.get_secret_value().strip()):
        log.warning("LangSmith tracing requested but LANGSMITH_API_KEY is not set; tracing stays off")
        return []
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import Client

    client = Client(
        api_key=settings.langsmith_api_key.get_secret_value(),
        hide_inputs=redact_for_trace,
        hide_outputs=redact_for_trace,
        hide_metadata=redact_for_trace,
    )
    log.info("LangSmith tracing enabled with content redaction")
    return [LangChainTracer(client=client, project_name="chotubot")]
