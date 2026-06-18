#!/usr/bin/env python3
"""
Test Council — single orchestrator that owns the engine and runs every
gate as a member.

Boots the Jambubrowser engine once (or reuses one already on :8001), then
runs a registry of gates. Each gate is a self-contained subprocess
that returns 0 on pass. The council:

  - runs gates sequentially (deterministic order)
  - captures wall-clock + last 4 KB of stdout/stderr per gate
  - writes a JSON report to tests/.artifacts/council.json
  - prints a final pass/fail/skip summary
  - tears the engine down on exit (unless it was reused)

Adding a new gate = one entry in the GATES list. No subclassing, no
plugin loader, no ceremony. The gate is just a command.

Run:
    python3 tests/council.py              # full suite (~60s under real LLM)
    python3 tests/council.py --gate X     # run just one gate by name
    python3 tests/council.py --no-engine  # skip the engine boot
    python3 tests/council.py --mock       # force mock provider (no real LLM calls)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(REPO_ROOT / "mlx-venv" / "bin" / "python3")
ARTIFACT_DIR = REPO_ROOT / "tests" / ".artifacts"
ARTIFACT_PATH = ARTIFACT_DIR / "council.json"

from _engine import managed_engine, ENGINE_URL  # noqa: E402

# ---------------------------------------------------------------------------
# Gate model
# ---------------------------------------------------------------------------

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class GateResult:
    name: str
    status: str             # PASS / FAIL / SKIP
    duration_s: float
    exit_code: int | None
    output_tail: str = ""    # last 4 KB of combined output
    skip_reason: str | None = None
    note: str | None = None


@dataclass
class Gate:
    name: str
    description: str
    # The command to run (list form, no shell).
    cmd: list[str]
    cwd: Path = REPO_ROOT
    # Env overrides (merged with the council env).
    env: dict[str, str] = field(default_factory=dict)
    # If set and returns a non-empty string, the gate is SKIPPED with
    # the returned string as the reason.
    skip_if: Callable[[dict[str, str]], str | None] | None = None
    # Per-gate timeout in seconds.
    timeout_s: int = 300


# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------

def _skip_if_not_real_provider(_env: dict[str, str]) -> str | None:
    """Skip the gate unless JAMBU_LLM_PROVIDER points at a real LLM."""
    p = _env.get("JAMBU_LLM_PROVIDER", "mock")
    if p in ("", "mock", "auto"):
        return "JAMBU_LLM_PROVIDER=mock (set provider to enable real-LLM gate)"
    return None


def _skip_if_claude_missing(_env: dict[str, str]) -> str | None:
    if shutil.which("claude") is None:
        return "claude CLI not on PATH"
    return None


def _skip_if_npm_missing(_env: dict[str, str]) -> str | None:
    if shutil.which("npm") is None:
        return "npm not on PATH"
    return None


# The canonical gate list. Adding a gate = one entry, no other ceremony.
GATES: list[Gate] = [
    Gate(
        name="engine_health",
        description="Engine /health probe (asserts the boot succeeded).",
        cmd=[PYTHON, "-c",
             "import urllib.request, sys; "
             "r = urllib.request.urlopen('" + ENGINE_URL + "/health', timeout=5); "
             "sys.exit(0 if r.status == 200 else 1)"],
        timeout_s=10,
    ),
    Gate(
        name="harness_bench_SUB-H",
        description="HarnessX bench SUB-H: real-LLM Planner + Critic. "
                    "Skips under mock.",
        cmd=[PYTHON, str(REPO_ROOT / "tests" / "bench_harness_efficiency.py"),
             "--sub-h-only"],
        env={"JAMBU_LLM_PROVIDER": os.environ.get("JAMBU_LLM_PROVIDER", "mock")},
        skip_if=_skip_if_not_real_provider,
        timeout_s=240,
    ),
    Gate(
        name="browser_real_llm_vitest",
        description="Browser-app vitest real-LLM integration gate. Skips under mock.",
        cmd=["npm", "test", "--silent", "--",
             "src/test/llm-integration.test.ts"],
        cwd=REPO_ROOT / "browser-app",
        skip_if=_skip_if_npm_missing,
        timeout_s=180,
    ),
    Gate(
        name="mcp_server_stdio_smoke",
        description="MCP server stdio smoke (no engine required). Always runs.",
        cmd=[PYTHON, "-m", "pytest",
             str(REPO_ROOT / "tests" / "test_mcp_server.py"),
             "-q", "--tb=short"],
        timeout_s=120,
    ),
    Gate(
        name="mcp_live_e2e",
        description="MCP server live e2e: spawns the server, drives tools against "
                    "the live engine. Skips if engine is unreachable.",
        cmd=[PYTHON, "-m", "pytest",
             str(REPO_ROOT / "tests" / "test_mcp_server.py::test_mcp_live_e2e"),
             "-v"],
        env={"JAMBU_MCP_LIVE": "1"},
        timeout_s=180,
    ),
    Gate(
        name="mcp_via_claude_cli",
        description="Drive the MCP server through the Claude Code CLI. "
                    "Skips if claude CLI is missing.",
        cmd=[PYTHON, str(REPO_ROOT / "tests" / "bench_claude_cli_mcp.py")],
        skip_if=_skip_if_claude_missing,
        timeout_s=180,
    ),
    Gate(
        name="harness_x_smoke",
        description="HarnessX end-to-end smoke (7 sub-reports under mock). "
                    "Always runs.",
        cmd=[PYTHON, str(REPO_ROOT / "tests" / "smoke_harnessx_e2e.py")],
        env={"JAMBU_LLM_PROVIDER": "mock"},
        timeout_s=120,
    ),
    Gate(
        name="browser_unit_tests",
        description="Browser vitest unit tests (appStore, api). Always runs.",
        cmd=["npm", "test", "--silent"],
        cwd=REPO_ROOT / "browser-app",
        skip_if=_skip_if_npm_missing,
        timeout_s=120,
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_subprocess(cmd: list[str], cwd: Path, env: dict[str, str],
                    timeout_s: int) -> tuple[int, str, float]:
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s,
        )
        return result.returncode, (result.stdout + result.stderr)[-4000:], time.time() - t0
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout_s}s", time.time() - t0
    except FileNotFoundError as e:
        return -1, f"command not found: {e}", time.time() - t0


def _evaluate_gate(gate: Gate, base_env: dict[str, str], name_filter: str | None) -> GateResult:
    if name_filter and gate.name != name_filter:
        return GateResult(name=gate.name, status=SKIP, duration_s=0.0, exit_code=None,
                          skip_reason="not in --gate filter")

    # Build the effective env (base + per-gate overrides)
    env = base_env.copy()
    env.update(gate.env)

    # Skip predicate
    if gate.skip_if is not None:
        reason = gate.skip_if(env)
        if reason is not None:
            return GateResult(name=gate.name, status=SKIP, duration_s=0.0, exit_code=None,
                              skip_reason=reason)

    print(f"\n[gate] {gate.name}  ({gate.description})")
    rc, output, elapsed = _run_subprocess(gate.cmd, gate.cwd, env, gate.timeout_s)
    status = PASS if rc == 0 else FAIL
    result = GateResult(name=gate.name, status=status, duration_s=elapsed,
                         exit_code=rc, output_tail=output)
    suffix = _colorize(status, f"{status.upper()} ({elapsed:.1f}s)")
    print(f"  {suffix}")
    if status == FAIL:
        # Print last 12 lines for context
        for line in output.strip().splitlines()[-12:]:
            print(f"      {line}")
    return result


def _colorize(status: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    codes = {PASS: "32", FAIL: "31", SKIP: "33"}
    code = codes.get(status, "0")
    return f"\033[{code}m{text}\033[0m"


def _print_report(results: list[GateResult]) -> None:
    print("\n" + "=" * 72)
    print("COUNCIL REPORT")
    print("=" * 72)
    by_status: dict[str, list[GateResult]] = {PASS: [], FAIL: [], SKIP: []}
    for r in results:
        by_status[r.status].append(r)
    for status in (PASS, FAIL, SKIP):
        if not by_status[status]:
            continue
        print(f"  {_colorize(status, status.upper() + ':')}")
        for r in by_status[status]:
            extras = ""
            if r.status == SKIP and r.skip_reason:
                extras = f"  ({r.skip_reason})"
            elif r.status == FAIL and r.exit_code is not None:
                extras = f"  (exit {r.exit_code})"
            print(f"    - {r.name:<32} {r.duration_s:6.1f}s{extras}")
    print("-" * 72)
    n_pass = len(by_status[PASS])
    n_fail = len(by_status[FAIL])
    n_skip = len(by_status[SKIP])
    total = n_pass + n_fail + n_skip
    total_time = sum(r.duration_s for r in results)
    print(f"  Total: {n_pass} pass, {n_fail} fail, {n_skip} skip, "
          f"{total} gates, {total_time:.1f}s wall-clock")
    print("=" * 72)
    if n_fail == 0:
        print(_colorize(PASS, "\n✓ All gates passed (or skipped cleanly).\n"))
    else:
        print(_colorize(FAIL, f"\n✗ {n_fail} gate(s) failed.\n"))


def _write_artifact(results: list[GateResult], base_env: dict[str, str]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": base_env.get("JAMBU_LLM_PROVIDER", "mock"),
        "mode": "real" if base_env.get("JAMBU_LLM_PROVIDER", "mock") not in ("", "mock", "auto") else "mock",
        "engine_url": ENGINE_URL,
        "gates": [asdict(r) for r in results],
    }
    ARTIFACT_PATH.write_text(json.dumps(payload, indent=2, default=str))
    print(f"[artifact] wrote {ARTIFACT_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Jambubrowser test council.")
    parser.add_argument("--gate", help="Run only this gate (by name)")
    parser.add_argument("--no-engine", action="store_true",
                        help="Skip the engine boot (use an already-running engine)")
    parser.add_argument("--mock", action="store_true",
                        help="Force JAMBU_LLM_PROVIDER=mock (skip real-LLM gates cleanly)")
    parser.add_argument("--no-artifact", action="store_true",
                        help="Skip writing the JSON report to tests/.artifacts/")
    args = parser.parse_args()

    # Build the base env
    env = os.environ.copy()
    env.setdefault("JAMBU_DB_PATH", ":memory:")
    env.setdefault("JAMBU_VAULT_KEY", "test-key-do-not-use-in-production-32bytes!")
    if args.mock:
        env["JAMBU_LLM_PROVIDER"] = "mock"

    provider = env.get("JAMBU_LLM_PROVIDER", "mock")
    is_real = provider not in ("", "mock", "auto")
    print("=" * 72)
    print("JAMBUBROWSER TEST COUNCIL")
    print("=" * 72)
    print(f"  provider = {provider!r}  ({'real' if is_real else 'mock'})")
    print(f"  engine   = {ENGINE_URL}  (boot={'yes' if not args.no_engine else 'no'})")
    print(f"  python   = {PYTHON}")
    print(f"  gates    = {len(GATES)} registered, "
          f"{'all' if not args.gate else f'just {args.gate!r}'}")

    results: list[GateResult] = []
    cm = managed_engine(env) if not args.no_engine else _nullcontext(ENGINE_URL)
    with cm:
        for gate in GATES:
            results.append(_evaluate_gate(gate, env, args.gate))

    _print_report(results)
    if not args.no_artifact:
        _write_artifact(results, env)

    n_fail = sum(1 for r in results if r.status == FAIL)
    return 0 if n_fail == 0 else 1


class _nullcontext:
    """Like contextlib.nullcontext but takes a value to yield."""
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self.value
    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    sys.exit(main())
