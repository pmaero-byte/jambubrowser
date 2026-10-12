#!/usr/bin/env python3
"""Frozen entry point for the bundled engine sidecar.

This is what PyInstaller turns into the self-contained ``jambubrowser-engine``
binary shipped inside the macOS ``.app`` (via Tauri ``externalBin``). In a
frozen build every dependency and the whole ``backend`` package are embedded,
so the app can boot its engine on a machine with no Python, no checkout and
no venv — the gap the UI-only bundle used to have.

In dev (not frozen) it behaves like ``uvicorn backend.engine:app``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="jambubrowser-engine")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args()

    if not getattr(sys, "frozen", False):
        # Dev run from the checkout: make `backend` importable regardless of
        # cwd (the bundled .app launches sidecars from Contents/MacOS).
        root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root))

    import uvicorn

    uvicorn.run(
        "backend.engine:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
