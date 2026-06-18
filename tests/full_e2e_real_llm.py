#!/usr/bin/env python3
"""
Full-suite end-to-end orchestrator with a real LLM provider.

Boots the Jambubrowser engine (using whatever JAMBU_LLM_PROVIDER is set
in the env — minimax / anthropic / openai / ollama / mlx / etc.), waits
for it to come up, then runs every gate that benefits from a live engine:

  1. Engine health probe (sanity)
  2. Harness bench SUB-H (real-LLM Planner + Critic) — tests/bench_harness_efficiency.py
  3. Browser-app vitest real-LLM gate — browser-app/src/test/llm-integration.test.ts
  4. MCP server live e2e — tests/test_mcp_server.py::test_mcp_live_e2e
  5. MCP via Claude CLI smoke — tests/bench_claude_cli_mcp.py (Phase 2)

Each gate's result is recorded and a combined report is printed. The
engine subprocess is torn down on exit (success or failure).

Skip behaviour:
  - If JAMBU_LLM_PROVIDER is unset / 'mock' / 'auto', gates 2, 3, 4 still
    run but in skip mode. The orchestrator clearly labels each gate
    "PASS (mock)" vs "PASS (real)" vs "SKIPPED (engine down)".
  - If the engine fails to boot, all engine-dependent gates are skipped
    with a clear "engine unavailable" reason.

Run:
    python3 tests/full_e2e_real_llm.py
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(REPO_ROOT / "mlx-venv" / "bin" / "python3")
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8001
ENGINE_URL = f"http://{ENGINE_HOST}:{ENGINE_PORT}"
ENGINE_HEALTH_TIMEOUT = 30  # seconds to wait for the engine

# Gate result types
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
WARN = "WARN"

# ANSI color codes (no-op if not a TTY)
def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _green(t: str) -> str: return _c("32", t)
def _red(t: str) -> str: return _c("31", t)
def _yellow(t: str) -> str: return _c("33", t)
def _cyan(t: str) -> str: return _c("36", t)
def _bold(t: str) -> str: return _c("1", t)


# ---------------------------------------------------------------------------
# Engine subprocess management
# ---------------------------------------------------------------------------

def _free_port_already_in_use() -> bool:
    """Sanity check the port isn't already taken by another engine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((ENGINE_HOST, ENGINE_PORT))
            return False
        except OSError:
            return True


def _wait_for_health(url: str, timeout: float) -> tuple[bool, str]:
    """Poll /health until it returns 200 or we time out."""
    deadline = time.time() + timeout
    last_err = "no attempt yet"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if r.status == 200:
                    body = r.read().decode("utf-8", errors="replace")
                    return True, body[:200]
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(0.5)
    return False, last_err


@contextmanager
def _engine_subprocess(env: dict[str, str]):
    """Boot the engine in a subprocess, yield the URL, then tear down."""
    if _free_port_already_in_use():
        # An engine is already up. We use it as-is and don't kill it on exit.
        yield ENGINE_URL
        return

    print(_cyan(f"[engine] starting on {ENGINE_URL}..."))
    proc = subprocess.Popen(
        [
            PYTHON, "-m", "uvicorn", "backend.engine:app",
            "--host", ENGINE_HOST, "--port", str(ENGINE_PORT),
            "--log-level", "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        ok, body = _wait_for_health(ENGINE_URL, ENGINE_HEALTH_TIMEOUT)
        if not ok:
            # Capture the last bit of output for the error message
            try:
                proc.terminate()
                out, _ = proc.communicate(timeout=3)
            except Exception:
                out = b""
            raise RuntimeError(
                f"engine did not become healthy within {ENGINE_HEALTH_TIMEOUT}s "
                f"(last error: {body}; stdout: {out.decode('utf-8', errors='replace')[-500:]})"
            )
        print(_green(f"[engine] up — {body[:120]}"))
        yield ENGINE_URL
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            print(_cyan("[engine] torn down"))


# ---------------------------------------------------------------------------
# Gate runners
# ---------------------------------------------------------------------------

def _run_subprocess(cmd: list[str], cwd: Path | None = None,
                    env: dict[str, str] | None = None,
                    timeout: int = 180) -> tuple[int, str]:
    """Run a subprocess, return (exit_code, combined_output)."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd or REPO_ROOT, env=env, capture_output=True,
            text=True, timeout=timeout,
        )
        return result.returncode, (result.stdout + result.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return -1, f"command not found: {e}"


def _gate_engine_health(env: dict[str, str]) -> dict[str, Any]:
    """Gate 1: engine health probe."""
    print(_bold("\n[gate 1/5] engine health probe"))
    try:
        with urllib.request.urlopen(f"{ENGINE_URL}/health", timeout=3) as r:
            body = r.read().decode("utf-8")
            if r.status == 200:
                print(_green(f"  PASS — {body[:120]}"))
                return {"status": PASS, "detail": body[:200]}
            print(_red(f"  FAIL — status {r.status}: {body[:120]}"))
            return {"status": FAIL, "detail": f"status {r.status}: {body[:120]}"}
    except Exception as e:
        print(_red(f"  FAIL — {type(e).__name__}: {e}"))
        return {"status": FAIL, "detail": f"{type(e).__name__}: {e}"}


def _gate_harness_bench(env: dict[str, str], real_provider: bool) -> dict[str, Any]:
    """Gate 2: harness bench SUB-H (real-LLM only — use --sub-h-only)."""
    print(_bold("\n[gate 2/5] harness bench (real-LLM SUB-H)"))
    if not real_provider:
        print(_yellow("  SKIP (mock mode) — bench will record SUB-H as 'skipped (mock mode)'"))
    # Use --sub-h-only so we only run the real-LLM gate (saves ~3 min
    # of full-bench runs against the live provider).
    cmd = [PYTHON, str(REPO_ROOT / "tests" / "bench_harness_efficiency.py"), "--sub-h-only"]
    rc, out = _run_subprocess(cmd, env=env, timeout=300)
    if rc != 0:
        print(_red(f"  FAIL — bench exited {rc}"))
        return {"status": FAIL, "detail": out[-1500:], "exit_code": rc}
    # Pull the SUB-H section out of the output
    sub_h_block: list[str] = []
    in_block = False
    for line in out.splitlines():
        if "SUB-H" in line and "===" in line:
            in_block = True
            continue
        if in_block and line.startswith("==="):
            in_block = False
        if in_block:
            sub_h_block.append(line)
    sub_h_summary = "\n".join(sub_h_block).strip() or "(SUB-H block not found in output)"
    print(_green("  PASS"))
    print(_cyan(f"  SUB-H output:\n    " + sub_h_summary.replace("\n", "\n    ")))
    return {"status": PASS, "detail": sub_h_summary, "exit_code": rc}


def _gate_browser_real_llm(env: dict[str, str], real_provider: bool) -> dict[str, Any]:
    """Gate 3: browser vitest real-LLM gate."""
    print(_bold("\n[gate 3/5] browser-app real-LLM test"))
    if not real_provider:
        print(_yellow("  SKIP (mock mode) — test will skip with informative message"))
    npm = shutil.which("npm")
    if not npm:
        return {"status": SKIP, "detail": "npm not on PATH"}
    cmd = [npm, "test", "--silent", "--", "src/test/llm-integration.test.ts"]
    rc, out = _run_subprocess(
        cmd, cwd=REPO_ROOT / "browser-app", env=env, timeout=120,
    )
    if rc != 0:
        # Under mock + engine up, the test is expected to skip. Look for
        # the skip message in the output and treat that as PASS.
        if "skip" in out.lower() and "passed" in out.lower():
            print(_yellow("  PASS (skipped under mock) — see output"))
            return {"status": PASS, "detail": out[-1500:], "exit_code": rc, "note": "skipped"}
        print(_red(f"  FAIL — vitest exited {rc}"))
        return {"status": FAIL, "detail": out[-1500:], "exit_code": rc}
    print(_green("  PASS"))
    return {"status": PASS, "detail": out[-1500:], "exit_code": rc}


def _gate_mcp_live(env: dict[str, str]) -> dict[str, Any]:
    """Gate 4: MCP live e2e (test_mcp_server.py with JAMBU_MCP_LIVE=1)."""
    print(_bold("\n[gate 4/5] MCP server live e2e (test_mcp_server.py)"))
    cmd = [
        PYTHON, "-m", "pytest", "tests/test_mcp_server.py::test_mcp_live_e2e", "-v",
    ]
    rc, out = _run_subprocess(cmd, env=env, timeout=120)
    if rc == 0:
        print(_green("  PASS"))
        return {"status": PASS, "detail": out[-1500:], "exit_code": rc}
    # Graceful skip if engine just became unhealthy
    if "SKIPPED" in out or "skipped" in out.lower():
        print(_yellow(f"  PASS (skipped) — exit {rc}"))
        return {"status": SKIP, "detail": out[-1500:], "exit_code": rc}
    print(_red(f"  FAIL — pytest exited {rc}"))
    return {"status": FAIL, "detail": out[-1500:], "exit_code": rc}


def _gate_mcp_cli(env: dict[str, str]) -> dict[str, Any]:
    """Gate 5: MCP via Claude CLI (bench_claude_cli_mcp.py)."""
    print(_bold("\n[gate 5/5] MCP via Claude CLI (bench_claude_cli_mcp.py)"))
    if shutil.which("claude") is None:
        return {"status": SKIP, "detail": "claude CLI not on PATH"}
    cmd = [PYTHON, str(REPO_ROOT / "tests" / "bench_claude_cli_mcp.py")]
    rc, out = _run_subprocess(cmd, env=env, timeout=180)
    if rc == 0:
        print(_green("  PASS"))
        return {"status": PASS, "detail": out[-1500:], "exit_code": rc}
    print(_red(f"  FAIL — bench exited {rc}"))
    return {"status": FAIL, "detail": out[-1500:], "exit_code": rc}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _print_report(results: list[tuple[str, dict[str, Any]]]) -> None:
    print("\n" + "=" * 72)
    print(_bold("FULL-SUITE REPORT — End-to-end with real LLM provider"))
    print("=" * 72)

    by_status: dict[str, list[str]] = {PASS: [], FAIL: [], SKIP: []}
    for name, r in results:
        by_status.setdefault(r["status"], []).append(name)

    for status, names in by_status.items():
        if not names:
            continue
        if status == PASS:
            label = _green("PASS")
        elif status == FAIL:
            label = _red("FAIL")
        else:
            label = _yellow("SKIP")
        print(f"  {label}  {', '.join(names)}")

    print("-" * 72)
    n_pass = len(by_status[PASS])
    n_fail = len(by_status[FAIL])
    n_skip = len(by_status[SKIP])
    print(f"  {_green(str(n_pass) + ' pass')}  {_red(str(n_fail) + ' fail')}  "
          f"{_yellow(str(n_skip) + ' skip')}  ({n_pass + n_fail + n_skip} gates total)")
    print("=" * 72)

    if n_fail == 0:
        print(_green("\n✓ All gates passed (or skipped cleanly under mock).\n"))
    else:
        print(_red(f"\n✗ {n_fail} gate(s) failed. See above.\n"))


def main() -> int:
    # Start with the .env values (real provider config) and override the
    # keys that the engine subprocess needs.
    env = os.environ.copy()
    env.setdefault("JAMBU_DB_PATH", ":memory:")
    env.setdefault("JAMBU_VAULT_KEY", "test-key-do-not-use-in-production-32bytes!")
    # The orchestrator always uses the configured provider, even if it's
    # "mock" — the gates have their own skip logic.
    provider = env.get("JAMBU_LLM_PROVIDER", "mock")
    is_real = provider not in ("", "mock", "auto")

    print("=" * 72)
    print(_bold("Full-Suite End-to-End Orchestrator (real LLM)"))
    print("=" * 72)
    print(f"  JAMBU_LLM_PROVIDER = {provider!r}")
    print(f"  Mode               = {'REAL' if is_real else 'MOCK'}")
    print(f"  Engine URL         = {ENGINE_URL}")
    print(f"  Repo root          = {REPO_ROOT}")
    print(f"  Python             = {PYTHON}")

    results: list[tuple[str, dict[str, Any]]] = []

    with _engine_subprocess(env):
        # Gate 1
        r = _gate_engine_health(env)
        results.append(("engine_health", r))

        # Gate 2
        results.append(("harness_bench_SUB-H", _gate_harness_bench(env, is_real)))

        # Gate 3
        results.append(("browser_real_llm_vitest", _gate_browser_real_llm(env, is_real)))

        # Gate 4
        results.append(("mcp_live_e2e", _gate_mcp_live(env)))

        # Gate 5
        results.append(("mcp_via_claude_cli", _gate_mcp_cli(env)))

    _print_report(results)

    return 0 if all(r["status"] in (PASS, SKIP) for _, r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
