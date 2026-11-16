"""Aggregation, gates and rendering for evaluation results (P1-T29)."""
from __future__ import annotations

import hashlib
import math
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.evaluators.dataset import CATEGORY_TARGETS, PENDING, Dataset
from evals.evaluators.runner import CaseResult

TOOL_SELECTION_THRESHOLD = 0.90  # P1-REQ-22


def distribution(values: list[float]) -> dict[str, Any]:
    vs = sorted(v for v in values if v is not None)
    if not vs:
        return {"n": 0}

    def pct(p: float) -> float:
        return vs[max(0, math.ceil(p / 100 * len(vs)) - 1)]

    return {"n": len(vs), "min": vs[0], "p50": pct(50), "p95": pct(95), "max": vs[-1], "mean": sum(vs) / len(vs), "total": sum(vs)}


def _rate(flags: list[bool | None]) -> dict[str, Any]:
    app = [f for f in flags if f is not None]
    ok = sum(1 for f in app if f)
    return {"applicable": len(app), "correct": ok, "accuracy": (ok / len(app)) if app else None}


def _git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip() + (" (+uncommitted changes)" if dirty.stdout.strip() else "")
    except Exception:
        pass
    return None


def build_report(ds: Dataset, ds_path: Path, results: list[CaseResult], mode: str, repo_root: Path) -> dict[str, Any]:
    executed = [r for r in results if r.executed]
    skipped = [r for r in results if not r.executed]
    by_cat: dict[str, Any] = {}
    for cat in CATEGORY_TARGETS:
        rs = [r for r in executed if r.category == cat]
        by_cat[cat] = {
            "cases": len([r for r in results if r.category == cat]),
            "tool_selection": _rate([r.tool_selection_correct for r in rs]),
            "argument_correctness": _rate([r.argument_correct for r in rs]),
            "task_success": _rate([r.task_success for r in rs]),
        }
    sel = _rate([r.tool_selection_correct for r in executed])
    critical = [r.id for r in executed if r.critical_failure]
    pending = [r.id for r in results if r.review_status == PENDING]
    incomplete = [r.id for r in skipped if (r.skipped_reason or "").startswith("run stopped")]
    # Frozen mode copies the model's choices from the same file as the expectations, so ~100% is guaranteed by
    # construction. The 90% gate is only meaningful for a complete live run.
    if mode == "frozen":
        sel_pass = None
    else:
        sel_pass = sel["accuracy"] is not None and sel["accuracy"] >= TOOL_SELECTION_THRESHOLD and not incomplete
    all_pass = all(r.task_success for r in executed)
    return {
        "mode": mode,
        "mode_note": (
            "FROZEN: scripted provider responses. Verifies validation, tool authorization, error handling, persistence and "
            "usage records end to end. It does NOT measure real-model tool-selection quality; that needs a live run."
            if mode == "frozen" else
            "LIVE: real NVIDIA NIM model. Results depend on the model and use your provider quota."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(repo_root),
        "python": platform.python_version(),
        "dataset": {"name": ds.dataset, "version": ds.version, "path": str(ds_path.relative_to(repo_root)).replace("\\", "/"),
                    "sha256": hashlib.sha256(ds_path.read_bytes()).hexdigest(),
                    "category_counts": ds.category_counts(), "matches_spec_distribution": ds.distribution_matches_spec()},
        "totals": {"cases": len(results), "executed": len(executed), "skipped": len(skipped),
                   "task_success_passed": sum(1 for r in executed if r.task_success), "failed": sum(1 for r in executed if not r.task_success)},
        "metrics": {
            "tool_selection": sel,
            "argument_correctness": _rate([r.argument_correct for r in executed]),
            "task_success": _rate([r.task_success for r in executed]),
            "latency_ms": distribution([r.latency_ms for r in executed if r.latency_ms is not None]),
            "tokens": distribution([r.total_tokens for r in executed if r.total_tokens is not None]),
            "latency_note": "frozen-mode latency/tokens come from scripted responses and are not representative of a real model" if mode == "frozen" else "measured against the real provider",
            "by_category": by_cat,
        },
        "gates": {
            "tool_selection_at_least_90pct": {"threshold": TOOL_SELECTION_THRESHOLD, "value": sel["accuracy"], "pass": sel_pass},
            "no_critical_security_failures": {"count": len(critical), "cases": critical, "pass": not critical},
            "all_executed_cases_pass": {"pass": all_pass},
            "human_review_complete": {"pending": len(pending), "reviewed": len(results) - len(pending), "pass": not pending},
            "dataset_matches_8_10_6_6": {"pass": ds.distribution_matches_spec()},
        },
        "not_covered_here": [
            "Live-model tool-selection accuracy (run with --mode live; uses your provider quota)." if mode == "frozen" else "Frozen-only fault cases (outage, malformed output) are skipped in live mode.",
            "Mandatory deterministic pytest suite (run `python -m pytest`).",
            "Independent evaluator approval (P1-REQ-20).",
        ],
        "failures": [{"id": r.id, "category": r.category, "reasons": r.failures} for r in executed if not r.task_success],
        "skipped": [{"id": r.id, "reason": r.skipped_reason} for r in skipped],
        "cases": [r.to_dict() for r in results],
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_markdown(rep: dict[str, Any]) -> str:
    m, g, t = rep["metrics"], rep["gates"], rep["totals"]
    L = [f"# Phase 1 evaluation report ({rep['mode'].upper()})", "",
         f"- Generated: {rep['generated_at']}  |  Python {rep['python']}  |  commit: {rep['git_commit'] or 'not a git commit'}",
         f"- Dataset: `{rep['dataset']['path']}` v{rep['dataset']['version']}  (sha256 `{rep['dataset']['sha256'][:16]}...`)",
         f"- {rep['mode_note']}", "",
         f"**Cases:** {t['cases']} total, {t['executed']} executed, {t['skipped']} skipped, {t['task_success_passed']} passed, {t['failed']} failed", "",
         "## Metrics", "", "| Metric | Correct / applicable | Rate |", "|---|---|---|"]
    for key, label in (("tool_selection", "Tool selection"), ("argument_correctness", "Argument correctness"), ("task_success", "Task success")):
        d = m[key]
        L.append(f"| {label} | {d['correct']} / {d['applicable']} | {_pct(d['accuracy'])} |")
    L += ["", "| Category | Cases | Tool selection | Arguments | Task success |", "|---|---|---|---|---|"]
    for cat, d in m["by_category"].items():
        L.append(f"| {cat} | {d['cases']} | {_pct(d['tool_selection']['accuracy'])} | {_pct(d['argument_correctness']['accuracy'])} | {_pct(d['task_success']['accuracy'])} |")
    for key, unit in (("latency_ms", "ms"), ("tokens", "tokens")):
        d = m[key]
        if d["n"]:
            L += ["", f"**{key}** (n={d['n']}): min {d['min']:.4g}, p50 {d['p50']:.4g}, p95 {d['p95']:.4g}, max {d['max']:.4g}, mean {d['mean']:.4g}, total {d['total']:.4g} {unit}"]
    L += ["", f"_{m['latency_note']}_", "", "## Gates", "", "| Gate | Result |", "|---|---|"]
    ts = g["tool_selection_at_least_90pct"]
    if ts["pass"] is None:
        L.append(f"| Tool selection >= 90% | N/A in frozen mode (scripted answers match by construction: {_pct(ts['value'])}); needs a live run |")
    else:
        L.append(f"| Tool selection >= 90% | {'PASS' if ts['pass'] else 'FAIL'} ({_pct(ts['value'])}; live) |")
    L.append(f"| No critical security failures | {'PASS' if g['no_critical_security_failures']['pass'] else 'FAIL ' + str(g['no_critical_security_failures']['cases'])} |")
    L.append(f"| Every executed case passes | {'PASS' if g['all_executed_cases_pass']['pass'] else 'FAIL'} |")
    hr = g["human_review_complete"]
    L.append(f"| Golden cases human-reviewed | {'PASS' if hr['pass'] else 'NOT MET'} ({hr['pending']} pending, {hr['reviewed']} reviewed) |")
    L.append(f"| Dataset is 8/10/6/6 | {'PASS' if g['dataset_matches_8_10_6_6']['pass'] else 'FAIL'} |")
    L += ["", "**This report is not an acceptance decision.** Still required: " + "; ".join(rep["not_covered_here"]), "", "## Failures", ""]
    if rep["failures"]:
        for f in rep["failures"]:
            L.append(f"- `{f['id']}` ({f['category']}): " + "; ".join(f["reasons"]))
    else:
        L.append("None among executed cases.")
    if rep["skipped"]:
        L += ["", "## Skipped", ""] + [f"- `{s['id']}`: {s['reason']}" for s in rep["skipped"]]
    L += ["", "## Per-case results", "", "| Case | Review | HTTP | Tools (status) | Selection | Args | Success | ms | tokens |", "|---|---|---|---|---|---|---|---|---|"]
    for c in rep["cases"]:
        tools = ", ".join(f"{x['name']}({x['status']})" for x in c["tools"]) or "-"
        mark = lambda v: "-" if v is None else ("yes" if v else "NO")
        L.append(f"| {c['id']} | {'pending' if c['review_status'] == PENDING else 'reviewed'} | {c['http_status'] if c['executed'] else 'skipped'} | {tools} | {mark(c['tool_selection_correct'])} | {mark(c['argument_correct'])} | {mark(c['task_success'])} | {c['latency_ms'] if c['latency_ms'] is not None else '-'} | {c['total_tokens'] if c['total_tokens'] is not None else '-'} |")
    return "\n".join(L) + "\n"
