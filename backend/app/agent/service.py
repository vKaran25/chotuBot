"""Chat orchestration: validate -> graph -> persist -> usage record."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator

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

    def chat(self, message: str, session_id: str | None, context: Any = None) -> dict[str, Any]:
        """`context` (Phase 2, optional) adapts one turn: `model()` may supply an offline model, `model_input()`
        rewrites only what the model sees for this message, `finalize()` post-processes the reply.
        With `context=None` this is exactly the Phase 1 behavior."""
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
                model = context.model(self.settings, model_id) if context is not None else None
                if model is None:
                    model = self.model_factory(self.settings, model_id)
                history = self.storage.recent_messages(sid, self.settings.history_message_limit) if sid else []
                graph = build_graph(model, self.registry, self.settings.max_tool_rounds, ai_log, activity)
                msgs = [HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"]) for m in history]
                msgs.append(HumanMessage(context.model_input(message) if context is not None else message))
                final = graph.invoke(
                    {"messages": msgs, "rounds": 0},
                    config={"callbacks": self.callbacks, "recursion_limit": 25},
                )
                last = final["messages"][-1]
                if getattr(last, "tool_calls", None):
                    raise AgentLoopLimit()
                text = reply_text(last)[:MAX_REPLY_CHARS]
                if context is not None:
                    text = context.finalize(text)
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

    def chat_stream(self, message: str, session_id: str | None, context: Any = None) -> Iterator[dict[str, Any]]:
        """Streaming variant of `chat` (owner-approved 2026-11-19; docs/specs/chat.md "Streaming").

        Everything that can fail before the model runs (unknown session, missing key, model not allowed) raises
        `ChatError` here, eagerly, so the HTTP layer can still answer with a normal JSON error. The returned
        iterator yields `delta` / `reset` events and ends with exactly one `done` or `error` event. Persistence and
        usage records follow the same rules as `chat`.
        """
        if session_id is not None and not self.storage.session_exists(session_id):
            raise ChatError(404, "session_not_found", "No such conversation.")
        request_id = new_id()
        model_id = self.effective_model()
        started = time.perf_counter()
        ai_log: list[AIMessage] = []
        activity: list[dict[str, Any]] = []
        try:
            model = context.model(self.settings, model_id) if context is not None else None
            if model is None:
                model = self.model_factory(self.settings, model_id)
        except (MissingApiKey, ModelNotAllowed) as e:
            err = (ChatError(503, "missing_api_key", f"No API key is configured on the backend. Set {self.settings.key_env_var} in .env and restart.")
                   if isinstance(e, MissingApiKey)
                   else ChatError(503, "model_unavailable", "The selected model is not in the allowed tool-capable model list."))
            self._record_usage(request_id, session_id, model_id, ai_log, activity, started, _status_for(err.code), err.code)
            raise err from e
        history = self.storage.recent_messages(session_id, self.settings.history_message_limit) if session_id else []
        graph = build_graph(model, self.registry, self.settings.max_tool_rounds, ai_log, activity)
        msgs = [HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"]) for m in history]
        msgs.append(HumanMessage(context.model_input(message) if context is not None else message))
        run = (graph, msgs, message, session_id, context, request_id, model_id, started, ai_log, activity)
        return self._stream_events(*run)

    def _stream_events(self, graph, msgs, message, session_id, context, request_id, model_id, started, ai_log, activity):
        def fail(status: str, code: str) -> None:
            self._record_usage(request_id, session_id, model_id, ai_log, activity, started, status, code)

        try:
            final_state: dict[str, Any] | None = None
            streamed = False
            for mode, chunk in graph.stream(
                {"messages": msgs, "rounds": 0},
                config={"callbacks": self.callbacks, "recursion_limit": 25},
                stream_mode=["messages", "values"],
            ):
                if mode == "values":
                    final_state = chunk
                    continue
                msg, meta = chunk
                if meta.get("langgraph_node") != "llm_call" or not isinstance(msg, AIMessage):
                    continue
                if getattr(msg, "tool_calls", None) or getattr(msg, "tool_call_chunks", None):
                    if streamed:  # text written before a tool call is not the answer
                        yield {"type": "reset"}
                        streamed = False
                    continue
                piece = _content_text(msg.content)
                if piece:
                    streamed = True
                    yield {"type": "delta", "text": piece}
            last = final_state["messages"][-1]
            if getattr(last, "tool_calls", None):
                raise AgentLoopLimit()
            text = reply_text(last)[:MAX_REPLY_CHARS]
            if context is not None:
                text = context.finalize(text)
            sid = self.storage.save_exchange(session_id, message, text, activity, request_id)
        except ChatError as e:
            fail(_status_for(e.code), e.code)
            yield {"type": "error", "error": {"code": e.code, "message": e.message}}
            return
        except GeneratorExit:  # client went away before the answer was saved
            fail("error", "client_disconnected")
            raise
        except Exception:
            log.exception("streamed chat failed")
            fail("error", "internal_error")
            yield {"type": "error", "error": {"code": "internal_error", "message": "An internal error occurred."}}
            return
        usage = self._record_usage(request_id, sid, model_id, ai_log, activity, started, "ok", None)
        yield {"type": "done", "response": {
            "session_id": sid,
            "request_id": request_id,
            "message": {"role": "assistant", "content": text},
            "tool_activity": activity,
            "usage": usage,
        }}

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


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return ""


def _status_for(code: str) -> str:
    return {
        "missing_api_key": "missing_key",
        "provider_unavailable": "provider_error",
        "malformed_model_output": "malformed_output",
        "agent_loop_limit": "loop_limit",
    }.get(code, "error")
