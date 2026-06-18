"""
Engine subprocess manager — shared by tests/council.py and
tests/full_e2e_real_llm.py.

Boots the Jambubrowser engine in a subprocess, waits for it to be
healthy, yields the URL, and tears the process down on exit. Idempotent
if the port is already in use (uses the running engine as-is).
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(REPO_ROOT / "mlx-venv" / "bin" / "python3")
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8001
ENGINE_URL = f"http://{ENGINE_HOST}:{ENGINE_PORT}"
DEFAULT_HEALTH_TIMEOUT = 30.0


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _wait_for_health(url: str, timeout: float) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last_err = "no attempt yet"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if r.status == 200:
                    body = r.read().decode("utf-8", errors="replace")
                    return True, body[:300]
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(0.5)
    return False, last_err


@contextmanager
def managed_engine(
    env: dict[str, str] | None = None,
    *,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
    label: str = "engine",
) -> Generator[str, None, None]:
    """Boot the engine (or reuse a running one), yield the URL, tear down.

    If port 8001 is already in use, the context manager yields immediately
    without spawning a subprocess — useful when an outer orchestrator
    (or a developer's local terminal) has already started the engine.

    Args:
        env: environment for the subprocess (merged with os.environ).
        health_timeout: seconds to wait for /health.
        label: human-readable label for log lines.

    Yields:
        The engine URL (e.g. http://127.0.0.1:8001).
    """
    if _port_in_use(ENGINE_HOST, ENGINE_PORT):
        print(f"[{label}] reusing already-running engine at {ENGINE_URL}")
        yield ENGINE_URL
        return

    env = env or os.environ.copy()
    print(f"[{label}] starting on {ENGINE_URL}...")
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
        ok, body = _wait_for_health(ENGINE_URL, health_timeout)
        if not ok:
            try:
                proc.terminate()
                out, _ = proc.communicate(timeout=3)
            except Exception:
                out = b""
            raise RuntimeError(
                f"{label} did not become healthy within {health_timeout}s "
                f"(last error: {body}; stdout: {out.decode('utf-8', errors='replace')[-500:]})"
            )
        print(f"[{label}] up — {body[:120]}")
        yield ENGINE_URL
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            print(f"[{label}] torn down")
