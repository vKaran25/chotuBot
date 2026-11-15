"""`python -m app` starts the API on loopback only (P1-REQ-1). Public hosting belongs to Phase 8."""
from __future__ import annotations

import argparse
import sys

import uvicorn

LOOPBACK = {"127.0.0.1", "localhost"}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Run the chotuBot backend (localhost only).")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    if args.host not in LOOPBACK:
        sys.exit(f"Refusing to bind to {args.host!r}: Phase 1 is localhost-only (use 127.0.0.1).")
    uvicorn.run("app.main:create_app", factory=True, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
