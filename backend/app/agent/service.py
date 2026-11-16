"""Chat orchestration: validate -> graph -> persist -> usage record."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import AgentLoopLimit, ChatError, build_graph, reply_text
from app.config import Settings
from app.models.provider import MissingApiKey, ModelNotAllowed, build_chat_model
from app.observability.usage import summarize
from app.storage import Storage, new_id, now_iso
from app.tools.registry import SampleToolRegistry

log = logging.getLogger(__name__)
ModelFactory = Callable[[Settings, str], BaseChatModel]
MAX_REPLY_CHARS = 20_000
MODEL_KEY = "model"  # app_settings key holding the owner's chosen model


class ChatService:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        registry: SampleToolRegistry,
        model_factory: ModelFactory = build_chat_model,
        callbacks: list[Any] | None = None,
    ):
        self.settings, self.storage, self.registry = settings, storage, registry
        self.model_factory = model_factory
        self.callbacks = callbacks or []
        self._models = settings.load_models()  # read once at startup

    def effective_model(self) -> str:
        allowed = [m.id for m in self._models if m.tool_capable]
        stored = self.storage.get_setting(MODEL_KEY)
        if stored in allowed:
            return stored
        if self.settings.default_model in allowed:
            return self.settings.default_model
        return allowed[0] if allowed else self.settings.default_model

    def chat(self, message: str, session_id: str | None) -> dict[str, Any]:
        if session_id is not None and not self.storage.session_exists(session_id):
            raise ChatError(404, "session_not_found", "No such conversation.")
        request_id = new_id()
        model_id = self.effective_model()
        started = time.perf_counter()
        ai_log: list[AIMessage] = []
        activity: list[dict[str, Any]] = []
        status, error_code = "ok", None
        sid = session_id
        result: dict[str, Any] | None = None
        usage: dict[str, Any] | None = None
        try:
            try:
                model = self.model_factory(self.settings, model_id)
                history = self.storage.recent_messages(sid, self.settings.history_message_limit) if sid else []
                graph = build_graph(model, self.registry, self.settings.max_tool_rounds, ai_log, activity)
                msgs = [HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"]) for m in history]
                msgs.append(HumanMessage(message))
                final = graph.invoke(
                    {"messages": msgs, "rounds": 0},
                    config={"callbacks": self.callbacks, "recursion_limit": 25},
                )
                last = final["messages"][-1]
                if getattr(last, "tool_calls", None):
                    raise AgentLoopLimit()
                text = reply_text(last)[:MAX_REPLY_CHARS]
                sid = self.storage.save_exchange(sid, message, text, activity, request_id)
                result = {"text": text}
            except MissingApiKey as e:
                raise ChatError(503, "missing_api_key", f"No API key is configured on the backend. Set {self.settings.key_env_var} in .env and restart.") from e
            except ModelNotAllowed as e:
                raise ChatError(503, "model_unavailable", "The selected model is not in the allowed tool-capable model list.") from e
        except ChatError as e:
            status, error_code = _status_for(e.code), e.code
            raise
        except Exception:
            status, error_code = "error", "internal_error"
            raise
        finally:
            usage = self._record_usage(request_id, sid, model_id, ai_log, activity, started, status, error_code)

        return {
            "session_id": sid,
            "request_id": request_id,
            "message": {"role": "assistant", "content": result["text"]},
            "tool_activity": activity,
            "usage": usage,
        }

    def _record_usage(self, request_id, sid, model_id, ai_log, activity, started, status, error_code) -> dict[str, Any]:
        s = summarize(ai_log)
        rec = {
            "request_id": request_id,
            "session_id": sid,
            "model": model_id,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "total_tokens": s.total_tokens,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status": status,
            "error_code": error_code,
            "tool_call_count": len(activity),
            "created_at": now_iso(),
        }
        try:
            self.storage.add_usage(rec)
        except Exception:  # never mask the real outcome of the chat request
            log.exception("failed to write usage record")
        return {k: rec[k] for k in (
            "model", "input_tokens", "output_tokens", "total_tokens", "latency_ms", "tool_call_count")}


def _status_for(code: str) -> str:
    return {
        "missing_api_key": "missing_key",
        "provider_unavailable": "provider_error",
        "malformed_model_output": "malformed_output",
        "agent_loop_limit": "loop_limit",
    }.get(code, "error")
