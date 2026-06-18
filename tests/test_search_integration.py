"""
Real-network search integration test.

Boots the engine, hits /search with a known query, and asserts the
fallback chain returns >= 1 result. This is the test that would have
caught the "0 results" bug surfaced in the deep-internet investigation.

Skips when:
  - The engine subprocess can't boot (covered by the council's engine
    health gate)
  - The user explicitly disables real-network tests
    (JAMBU_SKIP_NETWORK_TESTS=1)

Run:
    python3 tests/test_search_integration.py
or:
    JAMBU_SKIP_NETWORK_TESTS=0 python3 -m pytest tests/test_search_integration.py -v
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(REPO_ROOT / "mlx-venv" / "bin" / "python3")
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8001
ENGINE_URL = f"http://{ENGINE_HOST}:{ENGINE_PORT}"


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _wait_for_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _engine_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("JAMBU_DB_PATH", ":memory:")
    env.setdefault("JAMBU_VAULT_KEY", "test-key-do-not-use-in-production-32bytes!")
    # Honor the user's JAMBU_LLM_PROVIDER; default to mock so this
    # test passes in any environment. The search fallback chain
    # works under mock because the search code path doesn't touch
    # the LLM at all.
    env.setdefault("JAMBU_LLM_PROVIDER", "mock")
    return env


def _search(query: str) -> dict:
    """Hit /search and return the parsed JSON."""
    import json
    with urllib.request.urlopen(
        f"{ENGINE_URL}/search?q={query}&engines=bing,duckduckgo",
        timeout=15,
    ) as r:
        return json.loads(r.read())


def test_search_returns_results_for_known_query():
    """The fallback chain (SearXNG -> DDG -> Bing -> Google) must
    return at least one real result for a known-good query.

    Uses a session-scoped engine subprocess (booted once, reused
    across tests) to avoid the port-already-in-use race that
    would happen if each test booted its own.
    """
    if os.environ.get("JAMBU_SKIP_NETWORK_TESTS") == "1":
        import pytest
        pytest.skip("JAMBU_SKIP_NETWORK_TESTS=1")

    if not _wait_for_health(ENGINE_URL, timeout=5):
        # No engine — start one. Teardown is handled by pytest_sessionfinish.
        _start_session_engine()

    result = _search("python+asyncio")
    results = result.get("results", [])
    assert len(results) >= 1, (
        f"search returned 0 results for a known-good query. "
        f"Full response: {result}"
    )
    for r in results:
        assert r.get("url", "").startswith("http"), f"bad url: {r}"
        assert r.get("title", ""), f"empty title: {r}"
    print(f"  PASS — {len(results)} real search results, first: {results[0]['title'][:80]}")


def test_search_query_about_a_specific_concept():
    """A second, distinct query — should also return results."""
    if os.environ.get("JAMBU_SKIP_NETWORK_TESTS") == "1":
        import pytest
        pytest.skip("JAMBU_SKIP_NETWORK_TESTS=1")

    if not _wait_for_health(ENGINE_URL, timeout=5):
        _start_session_engine()

    result = _search("climate+change+2024")
    results = result.get("results", [])
    assert len(results) >= 1, \
        f"search returned 0 results for 'climate change 2024': {result}"
    print(f"  PASS — {len(results)} results for 'climate change 2024'")


# --- Session-scoped engine subprocess -----------------------------------------
# Pytest creates a fresh subprocess per test by default; if both tests
# try to bind :8001 the second races. Boot once at session start and
# tear down at session end.

_session_engine_proc: subprocess.Popen | None = None


def _start_session_engine() -> None:
    global _session_engine_proc
    if _session_engine_proc is not None and _session_engine_proc.poll() is None:
        return
    _session_engine_proc = subprocess.Popen(
        [
            PYTHON, "-m", "uvicorn", "backend.engine:app",
            "--host", ENGINE_HOST, "--port", str(ENGINE_PORT),
            "--log-level", "warning",
        ],
        cwd=str(REPO_ROOT),
        env=_engine_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    if not _wait_for_health(ENGINE_URL, timeout=30):
        _session_engine_proc.terminate()
        _session_engine_proc = None
        raise RuntimeError("engine did not become healthy within 30s")


def pytest_sessionfinish(session, exitstatus):
    global _session_engine_proc
    if _session_engine_proc is not None and _session_engine_proc.poll() is None:
        _session_engine_proc.terminate()
        try:
            _session_engine_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _session_engine_proc.kill()


if __name__ == "__main__":
    # Standalone runner for ad-hoc testing.
    print("=== Real-network search integration test ===")
    boot_proc = None
    if not _port_in_use(ENGINE_HOST, ENGINE_PORT):
        boot_proc = subprocess.Popen(
            [
                PYTHON, "-m", "uvicorn", "backend.engine:app",
                "--host", ENGINE_HOST, "--port", str(ENGINE_PORT),
                "--log-level", "warning",
            ],
            cwd=str(REPO_ROOT),
            env=_engine_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        if not _wait_for_health(ENGINE_URL, timeout=30):
            print("FAIL: engine did not boot")
            sys.exit(1)

    try:
        for q in ("python+asyncio", "climate+change+2024"):
            r = _search(q)
            n = len(r.get("results", []))
            print(f"  query={q!r} -> {n} results")
            if r.get("results"):
                print(f"    first: {r['results'][0]['title'][:80]}")
        print("PASS")
    finally:
        if boot_proc is not None and boot_proc.poll() is None:
            boot_proc.terminate()
            boot_proc.wait(timeout=5)
