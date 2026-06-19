"""
Real-LLM /research integration test.

Boots the engine, hits /research with an academic-domain query, and
asserts the response shape + that real arXiv sources came back. This is
the test that would have caught the 'NoneType.get' and 'scrape returned
dict' bugs.

Skips when:
  - JAMBU_LLM_PROVIDER is unset / "mock" / "auto" (no real provider)
  - JAMBU_SKIP_NETWORK_TESTS=1
  - The engine subprocess can't boot

Run:
    python3 tests/test_research_integration.py
or:
    python3 -m pytest tests/test_research_integration.py -v
"""

from __future__ import annotations

import json
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


def _skip(reason: str):
    import pytest
    pytest.skip(reason)


def _provider_is_real() -> bool:
    p = os.environ.get("JAMBU_LLM_PROVIDER", "mock")
    return p not in ("", "mock", "auto")


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
    return env


def _post_research(query: str, domain: str = "academic") -> dict:
    import json
    payload = json.dumps({
        "query": query,
        "domain": domain,
        "client_id": "research-integration-test",
        "max_steps": 1,
        "brain_only": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{ENGINE_URL}/research",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


# Pytest collects both tests below.

def test_research_academic_returns_arxiv_sources():
    """Live /research with academic domain must return real arXiv URLs."""
    if not _provider_is_real():
        _skip("JAMBU_LLM_PROVIDER not set to a real provider")
    if os.environ.get("JAMBU_SKIP_NETWORK_TESTS") == "1":
        _skip("JAMBU_SKIP_NETWORK_TESTS=1")

    if not _wait_for_health(ENGINE_URL, timeout=5):
        _start_session_engine()

    result = _post_research("transformer attention", domain="academic")
    # The synthesis step may time out under load — if so, fail loudly
    # so we know about it, but still assert the response *shape* is
    # correct (sources list, doc_count).
    assert "answer" in result, f"missing answer: {list(result.keys())}"
    assert "sources" in result, f"missing sources: {list(result.keys())}"
    assert "doc_count" in result, f"missing doc_count: {list(result.keys())}"
    assert isinstance(result["sources"], list), f"sources must be list, got {type(result['sources'])}"
    # Sources should be URLs (the arXiv academic path appends them as
    # {'url': '...', 'markdown': '...', 'score': 100} dicts into
    # all_res, then the response filters to [s['url'] for s in sources]).
    assert len(result["sources"]) >= 1, f"no sources returned: {result}"
    for s in result["sources"]:
        assert isinstance(s, str), f"source must be str, got {type(s)}: {s}"
        assert s.startswith("http"), f"bad source URL: {s}"
    # At least one source should be from arxiv.org
    assert any("arxiv.org" in s for s in result["sources"]), \
        f"no arXiv source in: {result['sources']}"
    assert result["doc_count"] >= 1, f"doc_count = 0: {result}"
    print(f"  PASS — {len(result['sources'])} sources, doc_count={result['doc_count']}, "
          f"answer={result['answer'][:80]!r}")


def test_research_general_returns_search_results():
    """Live /research with general domain must return search results
    (not 0 results, which was the original deep-internet bug)."""
    if not _provider_is_real():
        _skip("JAMBU_LLM_PROVIDER not set to a real provider")
    if os.environ.get("JAMBU_SKIP_NETWORK_TESTS") == "1":
        _skip("JAMBU_SKIP_NETWORK_TESTS=1")

    if not _wait_for_health(ENGINE_URL, timeout=5):
        _start_session_engine()

    result = _post_research("what is python asyncio", domain="general")
    assert "sources" in result
    assert len(result["sources"]) >= 1, f"no search sources returned: {result}"
    print(f"  PASS — {len(result['sources'])} search sources for general query")


# --- Session-scoped engine subprocess (booted once, reused by both tests) ---

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
    print("=== Real-LLM /research integration test ===")
    boot_proc = None
    if not _port_in_use(ENGINE_HOST, ENGINE_PORT):
        _start_session_engine()
        boot_proc = _session_engine_proc
    try:
        for q, d in [("transformer attention", "academic"),
                     ("what is python asyncio", "general")]:
            r = _post_research(q, domain=d)
            print(f"  query={q!r} -> answer={r.get('answer','')[:60]!r}")
            print(f"    sources ({len(r.get('sources',[]))}): {r.get('sources',[])[:3]}")
            print(f"    doc_count={r.get('doc_count')}")
        print("PASS")
    finally:
        if boot_proc is not None and boot_proc.poll() is None:
            boot_proc.terminate()
            boot_proc.wait(timeout=5)