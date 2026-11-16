"""Offline chat models: a scripted one for tests/evals and a rule-based one for the `demo` provider.

Neither ever touches the network. Routine tests and evaluations use these (P1-REQ-18).
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr


class ProviderOutage(Exception):
    """Frozen stand-in for a provider outage / network failure."""


def build_ai_message(spec: dict[str, Any]) -> AIMessage:
    """Build an AIMessage from a JSON-friendly spec used by frozen golden responses.

    spec: {content, tool_calls:[{name,args,id?}], input_tokens, output_tokens, cost_usd, raw_content}
    `raw_content` lets a frozen response be deliberately malformed (e.g. a non-string).
    """
    tool_calls = [
        {"name": tc["name"], "args": tc.get("args", {}), "id": tc.get("id", f"call_{i}"), "type": "tool_call"}
        for i, tc in enumerate(spec.get("tool_calls", []))
    ]
    content = spec["raw_content"] if "raw_content" in spec else spec.get("content", "")
    kwargs: dict[str, Any] = {"content": content, "tool_calls": tool_calls}
    if spec.get("invalid_tool_calls"):
        kwargs["invalid_tool_calls"] = spec["invalid_tool_calls"]
    i, o = spec.get("input_tokens"), spec.get("output_tokens")
    if i is not None or o is not None:
        kwargs["usage_metadata"] = {"input_tokens": i or 0, "output_tokens": o or 0, "total_tokens": (i or 0) + (o or 0)}
    if spec.get("cost_usd") is not None:
        kwargs["response_metadata"] = {"cost": spec["cost_usd"]}
    return AIMessage(**kwargs)


class ScriptedChatModel(BaseChatModel):
    """Returns pre-recorded responses in order. An `Exception` entry is raised instead."""

    model_name: str = "scripted"
    script: list[Any] = Field(default_factory=list, repr=False)
    _i: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        if self._i >= len(self.script):
            raise RuntimeError("ScriptedChatModel: script exhausted (model was called more often than expected)")
        item = self.script[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        msg = item if isinstance(item, AIMessage) else build_ai_message(item)
        return ChatResult(generations=[ChatGeneration(message=msg)])


_PROJECTS = ("atlas", "beacon", "cedar")
_STOP = {"the", "a", "an", "my", "me", "for", "in", "on", "of", "about", "any", "find", "search", "notes", "note",
         "please", "show", "what", "is", "are", "do", "i", "have", "with", "and", "to", "look", "up"}


class DemoChatModel(BaseChatModel):
    """Deterministic keyword-driven model so the UI can be exercised offline (CHOTU_PROVIDER=demo)."""

    model_name: str = "demo"

    @property
    def _llm_type(self) -> str:
        return "demo"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "DemoChatModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            msg = AIMessage(content=self._summarise(messages))
        else:
            text = str(last.content) if isinstance(last, HumanMessage) else ""
            low = text.casefold()
            if "status" in low or "project" in low:
                proj = next((p for p in _PROJECTS if p in low), None)
                args = {"project": proj} if proj else {}
                msg = AIMessage(content="", tool_calls=[{"name": "get_project_status", "args": args, "id": "demo_1", "type": "tool_call"}])
            elif "note" in low or "search" in low:
                words = [w for w in re.findall(r"[a-z0-9]+", low) if w not in _STOP]
                query = " ".join(words) or "notes"
                msg = AIMessage(content="", tool_calls=[{"name": "search_sample_notes", "args": {"query": query}, "id": "demo_1", "type": "tool_call"}])
            else:
                msg = AIMessage(content=f"(demo model) I can look up sample project status or search sample notes. You said: {text[:200]}")
        n_in = sum(len(str(m.content)) for m in messages) // 4 + 1
        n_out = len(str(msg.content)) // 4 + 1
        msg.usage_metadata = {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @staticmethod
    def _summarise(messages: list[BaseMessage]) -> str:
        parts = []
        for m in reversed(messages):
            if not isinstance(m, ToolMessage):
                break
            try:
                data = json.loads(str(m.content))
            except ValueError:
                data = {}
            if m.status == "error" or "error" in data:
                parts.append(f"The tool reported a problem: {data.get('error', m.content)}")
            elif "project" in data:
                p = data["project"]
                parts.append(f"{p['name'].title()} is {p['status'].replace('_', ' ')} ({p['progress_percent']}% complete). "
                             f"Owner {p['owner']}; next milestone: {p['next_milestone']} due {p['due_date']}.")
            elif "projects" in data:
                parts.append("Projects: " + "; ".join(f"{p['name']} is {p['status'].replace('_', ' ')}" for p in data["projects"]) + ".")
            elif "results" in data:
                titles = ", ".join(r["title"] for r in data["results"]) or "nothing"
                parts.append(f"Found {data['count']} note(s): {titles}.")
        return " ".join(reversed(parts)) or "Done."
