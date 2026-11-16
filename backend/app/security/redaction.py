"""Secret scrubbing for logs and redaction for external traces (P1-REQ-12, P1-REQ-13)."""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

REDACTED = "[REDACTED]"
_PATTERNS = [
    re.compile(r"lsv2_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"nvapi-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|authorization)\s*[=:]\s*\S+"),
]


def scrub(text: str, secrets: Iterable[str] = ()) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, REDACTED)
    for p in _PATTERNS:
        text = p.sub(REDACTED, text)
    return text


_installed_secrets: set[str] = set()
_factory_installed = False


def install_log_scrubber(secrets: Iterable[str] = ()) -> None:
    """Wrap the log-record factory so every log line (including uvicorn's) is scrubbed."""
    global _factory_installed
    _installed_secrets.update(s for s in secrets if s)
    if _factory_installed:
        return
    old = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        rec = old(*args, **kwargs)
        try:
            msg = rec.getMessage()
        except Exception:
            return rec
        clean = scrub(msg, _installed_secrets)
        if clean != msg:
            rec.msg, rec.args = clean, ()
        return rec

    logging.setLogRecordFactory(factory)
    _factory_installed = True


def redact_for_trace(obj: Any) -> Any:
    """Replace every string with a placeholder; keep structure, numbers and booleans.

    Used as LangSmith `hide_inputs` / `hide_outputs`, so message text, tool arguments, tool results
    and anything that looks like a credential never leave the machine.
    """
    if isinstance(obj, str):
        return REDACTED
    if isinstance(obj, dict):
        return {k: redact_for_trace(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [redact_for_trace(v) for v in obj]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return REDACTED
