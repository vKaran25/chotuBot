"""Two deterministic, read-only sample tools backed by fixtures (P1-REQ-5, P1-REQ-14)."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.security.tool_policy import ToolDenied, authorize_tool


class ToolExecutionError(Exception):
    """A permitted tool ran but could not produce a result (e.g. unknown project)."""


class GetProjectStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str | None = Field(
        default=None,
        max_length=64,
        description="Project name (e.g. 'atlas'). Omit to list the status of every project.",
    )


class SearchSampleNotesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200, description="Keywords to look for in the sample notes.")


_TOKEN = re.compile(r"[a-z0-9]+")
MAX_RESULTS = 5


class SampleToolRegistry:
    """Loads fixtures once; tools never touch the filesystem, network or any state after that."""

    def __init__(self, fixtures_dir: Path):
        projects = json.loads((fixtures_dir / "projects.json").read_text(encoding="utf-8"))
        notes = json.loads((fixtures_dir / "notes.json").read_text(encoding="utf-8"))
        self._projects: list[dict[str, Any]] = projects["projects"]
        self._notes: list[dict[str, Any]] = notes["notes"]

    # ---- tool implementations (pure functions of the fixtures) -----------
    def get_project_status(self, project: str | None = None) -> dict[str, Any]:
        if project is None or not project.strip():
            return {"projects": copy.deepcopy(self._projects)}
        wanted = project.strip().casefold()
        for rec in self._projects:
            if rec["name"].casefold() == wanted:
                return {"project": copy.deepcopy(rec)}
        known = ", ".join(p["name"] for p in self._projects)
        raise ToolExecutionError(f"Unknown project {project[:40]!r}. Known projects: {known}.")

    def search_sample_notes(self, query: str) -> dict[str, Any]:
        terms = [t for t in _TOKEN.findall(query.casefold()) if len(t) >= 2]
        if not terms:
            raise ToolExecutionError("The query has no searchable terms.")
        scored = []
        for n in self._notes:
            hay = " ".join([n["title"], n["body"], " ".join(n["tags"])]).casefold()
            hay_tokens = set(_TOKEN.findall(hay))
            score = sum(1 for t in terms if t in hay_tokens)
            if score:
                scored.append((-score, n["id"], n))
        scored.sort(key=lambda x: (x[0], x[1]))
        results = [copy.deepcopy(n) for _, _, n in scored[:MAX_RESULTS]]
        return {"query": query, "count": len(results), "results": results}

    # ---- exposure to the model --------------------------------------------
    def langchain_tools(self) -> list[StructuredTool]:
        """Schemas shown to the model. Execution still goes through `execute`."""
        return [
            StructuredTool.from_function(
                func=self.get_project_status,
                name="get_project_status",
                description="Read-only. Get the status of a sample project (atlas, beacon, cedar) or all projects.",
                args_schema=GetProjectStatusArgs,
            ),
            StructuredTool.from_function(
                func=self.search_sample_notes,
                name="search_sample_notes",
                description="Read-only. Keyword search over the owner's small set of sample notes.",
                args_schema=SearchSampleNotesArgs,
            ),
        ]

    # ---- the authorization + validation boundary ------------------------------
    def execute(self, call_id: str, name: Any, args: Any) -> dict[str, Any]:
        """Run one model-requested tool call; always returns an activity record, never raises."""
        activity: dict[str, Any] = {
            "id": call_id,
            "name": name if isinstance(name, str) else "<invalid>",
            "args": args if isinstance(args, dict) else {},
            "status": "ok",
            "result": None,
            "error": None,
        }
        try:
            authorize_tool(name)
            if name == "get_project_status":
                result = self.get_project_status(**GetProjectStatusArgs.model_validate(args).model_dump())
            else:
                result = self.search_sample_notes(**SearchSampleNotesArgs.model_validate(args).model_dump())
            activity["result"] = result
        except ToolDenied as e:
            activity.update(status="denied", error=str(e))
        except ValidationError as e:
            msgs = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'args'}: {x['msg']}" for x in e.errors())
            activity.update(status="error", error=f"Invalid arguments: {msgs}"[:300])
        except ToolExecutionError as e:
            activity.update(status="error", error=str(e))
        return activity
