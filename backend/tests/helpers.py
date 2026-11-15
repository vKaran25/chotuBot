from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.fakes import ScriptedChatModel


class ScriptedFactory:
    """Model factory that serves one scripted model per chat request and counts provider calls."""

    def __init__(self, *scripts: list[Any]):
        self.scripts = list(scripts)
        self.built = 0
        self.models: list[ScriptedChatModel] = []

    def __call__(self, settings: Settings, model_id: str) -> ScriptedChatModel:
        self.built += 1
        script = self.scripts.pop(0) if self.scripts else []
        m = ScriptedChatModel(script=script)
        self.models.append(m)
        return m


def make_client(settings: Settings, *scripts: list[Any], callbacks=None) -> tuple[TestClient, ScriptedFactory]:
    factory = ScriptedFactory(*scripts)
    app = create_app(settings, model_factory=factory, callbacks=callbacks if callbacks is not None else [])
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False), factory


def text(content: str, **kw: Any) -> dict[str, Any]:
    return {"content": content, "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0001, **kw}


def call(name: str, args: dict[str, Any], cid: str = "call_1", **kw: Any) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"name": name, "args": args, "id": cid}],
            "input_tokens": 20, "output_tokens": 8, "cost_usd": 0.0002, **kw}
