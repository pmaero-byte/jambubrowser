#!/usr/bin/env python3
"""
End-to-end orchestrator — thin wrapper around tests/council.py.

Preserves the original 5-gate UX for users who just want a single command:

    python3 tests/full_e2e_real_llm.py    # full suite with the configured LLM
    python3 tests/full_e2e_real_llm.py --mock  # force mock mode

The actual gate logic, engine boot/teardown, and report writing all live in
tests/council.py. This file is just a friendly entry point that runs the
council and prints the same combined report as before.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(REPO_ROOT / "mlx-venv" / "bin" / "python3")


def main() -> int:
    args = sys.argv[1:]
    # Pass through the mock flag if provided.
    cmd = [PYTHON, str(REPO_ROOT / "tests" / "council.py"), *args]
    print(f"[full_e2e_real_llm] delegating to: {cmd[-1]}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
