"""CLI: `python -m evals.evaluators.run [--mode frozen|live]`.

Frozen mode is free and deterministic. Live mode calls the real provider and is never run implicitly.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from app.config import REPO_ROOT
from evals.evaluators.dataset import PENDING, load_dataset
from evals.evaluators.report import build_report, render_markdown
from evals.evaluators.runner import dump_json, run_frozen, run_live_cases

DEFAULT_DATASET = REPO_ROOT / "evals" / "datasets" / "phase1-golden.v1.json"
REPORT_DIR = REPO_ROOT / "evals" / "reports"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the Phase 1 golden evaluation.")
    p.add_argument("--mode", choices=["frozen", "live"], default="frozen")
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p.add_argument("--confirm-live", action="store_true", help="required for live mode: acknowledges real provider calls")
    p.add_argument("--out", type=Path, default=REPORT_DIR)
    args = p.parse_args(argv)

    ds_path = args.dataset.resolve()
    ds = load_dataset(ds_path)
    if args.mode == "live" and not args.confirm_live:
        print("Live mode calls the real NVIDIA NIM API. Re-run with --confirm-live to proceed.", file=sys.stderr)
        return 2
    results = run_frozen(ds.cases) if args.mode == "frozen" else run_live_cases(ds.cases)
    rep = build_report(ds, ds_path, results, args.mode, REPO_ROOT)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    runs = args.out / "runs"
    runs.mkdir(exist_ok=True)
    md = render_markdown(rep)
    for base in (args.out / f"phase1-{args.mode}-latest", runs / f"phase1-{args.mode}-{stamp}"):
        base.with_suffix(".json").write_text(dump_json(rep), encoding="utf-8")
        base.with_suffix(".md").write_text(md, encoding="utf-8")

    t, g = rep["totals"], rep["gates"]
    sel = rep["metrics"]["tool_selection"]
    print(f"[{args.mode}] cases={t['cases']} executed={t['executed']} passed={t['task_success_passed']} failed={t['failed']} skipped={t['skipped']}")
    print(f"tool selection {sel['correct']}/{sel['applicable']}; critical security failures: {g['no_critical_security_failures']['count']}")
    pend = g["human_review_complete"]["pending"]
    if pend:
        print(f"WARNING: {pend} golden case(s) are {PENDING}; this run cannot satisfy P1-REQ-15 until the owner reviews them.")
    print(f"Report: {(args.out / f'phase1-{args.mode}-latest.md')}")
    # In frozen mode the selection gate is N/A (None) and does not affect the exit code.
    ok = (g["tool_selection_at_least_90pct"]["pass"] is not False
          and g["no_critical_security_failures"]["pass"] and g["all_executed_cases_pass"]["pass"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
