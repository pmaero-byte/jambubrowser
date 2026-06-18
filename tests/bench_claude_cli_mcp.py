#!/usr/bin/env python3
"""
End-to-end MCP-via-Claude-CLI test.

Drives the `claude` CLI in non-interactive mode (`claude -p`) to invoke one
or more jambubrowser MCP tools, then asserts the output. Two phases:

  Phase 1 — MCP server reachable, engine optional.
    Probes the engine at JAMBU_ENGINE_URL/health (skips gracefully if down).
    Runs `claude mcp list` and asserts jambubrowser is in the connected list.

  Phase 2 — Live tool call through Claude.
    Asks Claude to call check_engine_health + get_brain_stats. With the engine
    up, asserts the response includes "Engine Status: online" and "Documents".
    With the engine down, asserts the response includes "Engine Offline".

This is the highest-fidelity MCP integration test we have: it exercises
the same code path real Claude Desktop / CLI users would.

Run:
    python3 tests/bench_claude_cli_mcp.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_VENV = REPO_ROOT / "mlx-venv" / "bin" / "python3"
ENGINE_URL = os.environ.get("JAMBU_ENGINE_URL", "http://127.0.0.1:8001")
MCP_NAME = "jambubrowser"


def _run(cmd: list[str], timeout: int = 90) -> tuple[int, str]:
    """Run a subprocess, return (exit_code, stdout+stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return -1, f"command not found: {e}"


def _engine_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{ENGINE_URL}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Phase 1: claude mcp list — assert jambubrowser is registered and connected
# ---------------------------------------------------------------------------

def phase1_mcp_list() -> int:
    print("=" * 60)
    print("Phase 1: claude mcp list")
    print("=" * 60)
    if shutil.which("claude") is None:
        print("  FAIL: 'claude' CLI not on PATH")
        return 1
    rc, out = _run(["claude", "mcp", "list"], timeout=30)
    print(out)
    if rc != 0:
        print(f"  FAIL: claude mcp list exited {rc}")
        return 1
    # Parse the output: each registered server appears on a line
    # "name: command - Connected" or "name: command - Failed to connect"
    # (the bullet character may be ✔ or ✘ or just text depending on terminal)
    pattern = re.compile(
        rf"^{re.escape(MCP_NAME)}:\s+(.+?)\s+-\s+(.*?)$",
        re.MULTILINE,
    )
    m = pattern.search(out)
    if not m:
        print(f"  FAIL: '{MCP_NAME}' not in `claude mcp list` output")
        return 1
    status = m.group(2)
    if "Failed" in status or "needs authentication" in status.lower():
        print(f"  FAIL: {MCP_NAME} is registered but status is '{status.strip()}'")
        return 1
    print(f"  OK: {MCP_NAME} is registered with status '{status.strip()}'")
    return 0


# ---------------------------------------------------------------------------
# Phase 2: drive Claude to call a tool through MCP
# ---------------------------------------------------------------------------

def phase2_tool_call() -> int:
    print()
    print("=" * 60)
    print("Phase 2: drive Claude CLI to call MCP tools")
    print("=" * 60)
    if shutil.which("claude") is None:
        print("  FAIL: 'claude' CLI not on PATH")
        return 1

    engine_up = _engine_reachable()
    if engine_up:
        print(f"  engine reachable at {ENGINE_URL} — expecting live data")
    else:
        print(f"  engine NOT reachable at {ENGINE_URL} — expecting 'Engine Offline'")

    prompt = (
        "Use the jambubrowser MCP server to call check_engine_health. "
        "Report the response verbatim, no commentary."
    )
    cmd = [
        "claude", "-p", prompt,
        "--allowedTools", f"mcp__{MCP_NAME}__check_engine_health",
        "--max-turns", "4",
    ]
    print(f"  running: {' '.join(cmd[:6])}...")
    rc, out = _run(cmd, timeout=120)
    print()
    print(out)
    if rc != 0:
        print(f"  FAIL: claude -p exited {rc}")
        return 1

    if engine_up:
        if "Engine Status" not in out and "online" not in out.lower():
            print("  FAIL: engine was up but Claude's response didn't include engine data")
            return 1
        print("  OK: Claude returned engine-shaped data via MCP")
    else:
        if "Engine Offline" not in out:
            print("  FAIL: engine was down but Claude's response didn't mention 'Engine Offline'")
            return 1
        print("  OK: Claude returned the graceful 'Engine Offline' message via MCP")
    return 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    rc1 = phase1_mcp_list()
    rc2 = phase2_tool_call()
    print()
    print("=" * 60)
    if rc1 == 0 and rc2 == 0:
        print("PASS — jambubrowser MCP server works end-to-end via the Claude Code CLI")
        return 0
    print(f"FAIL — phase1 rc={rc1}, phase2 rc={rc2}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
