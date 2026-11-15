"""Chat orchestration: validate -> budget guard -> graph -> persist -> usage record."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import AgentLoopLimit, ChatError, build_graph, reply_text
from app.config import ModelInfo, Settings
from app.models.provider import MissingApiKey, ModelNotAllowed, build_chat_model
from app.observability.usage import summarize
from app.security.budget import MODEL_KEY, BudgetExceeded, BudgetGuard, month_key
from app.storage import Storage, new_id, now_iso
from app.tools.registry import SampleToolRegistry

log = logging.getLogger(__name__)
ModelFactory = Callable[[Settings, str], BaseChatModel]
MAX_REPLY_CHARS = 20_000


class ChatService:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        registry: SampleToolRegistry,
        model_factory: ModelFactory = build_chat_model,
        callbacks: list[Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.settings, self.storage, self.registry = settings, storage, registry
        self.model_factory = model_factory
        self.callbacks = callbacks or []
        self.clock = clock or (lambda: datetime.now(ZoneInfo("UTC")))
        self.guard = BudgetGuard(storage, settings, self.clock)
        self._models = settings.load_models()  # read once; usage recording must not depend on disk state

    def effective_model(self) -> str:
        allowed = [m.id for m in self._models if m.tool_capable and m.provider == self.settings.model_provider]
        stored = self.storage.get_setting(MODEL_KEY)
        if stored in allowed:
            return stored
        if self.settings.default_model in allowed:
            return self.settings.default_model
        return allowed[0] if allowed else self.settings.default_model

    def _model_info(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self._models if m.id == model_id), None)

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
                self.guard.check()
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
            except BudgetExceeded as e:
                raise ChatError(
                    402, "budget_exhausted",
                    f"The monthly model budget is used up (about INR {e.spent:.2f} of INR {e.limit:.2f}, an estimate). "
                    "No request was sent to the provider.",
                ) from e
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
        s = summarize(ai_log, self._model_info(model_id))
        rec = {
            "request_id": request_id,
            "session_id": sid,
            "model": model_id,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "total_tokens": s.total_tokens,
            "cost_usd": s.cost_usd,
            "cost_inr": s.cost_usd * self.settings.usd_to_inr,
            "cost_source": s.cost_source,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status": status,
            "error_code": error_code,
            "tool_call_count": len(activity),
            "month_key": month_key(self.clock(), self.settings.timezone),
            "created_at": now_iso(),
        }
        try:
            self.storage.add_usage(rec)
        except Exception:  # never mask the real outcome of the chat request
            log.exception("failed to write usage record")
        return {k: rec[k] for k in (
            "model", "input_tokens", "output_tokens", "total_tokens", "cost_usd", "cost_inr", "cost_source",
            "latency_ms", "tool_call_count")}


def _status_for(code: str) -> str:
    return {
        "budget_exhausted": "blocked_budget",
        "missing_api_key": "missing_key",
        "provider_unavailable": "provider_error",
        "malformed_model_output": "malformed_output",
        "agent_loop_limit": "loop_limit",
    }.get(code, "error")
