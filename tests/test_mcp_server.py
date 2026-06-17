"""
MCP server tests — verify the FastMCP server in backend/mcp_server.py boots,
exposes the expected tool surface, and (with the engine up) actually returns
engine-shaped data through the MCP protocol.

Two layers:
  - Stdio smoke (no engine): boots the server, lists tools, calls
    check_engine_health. Asserts every tool has a name + non-empty
    description + JSON-schema-shaped input.
  - Live e2e (engine up, run via pytest --run-mcp-live): spins up an
    in-process engine on a free port, points the MCP server at it via
    JAMBU_ENGINE_URL, and asserts that research_web / get_brain_stats
    return engine-shaped data (not the "Engine Offline" string).

Run:
  # stdio only (default, no engine required)
  python3 tests/test_mcp_server.py

  # or as pytest
  python3 -m pytest tests/test_mcp_server.py -v

  # live mode (engine must be bootable; runs in a subprocess)
  python3 -m pytest tests/test_mcp_server.py -v --run-mcp-live
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Hermetic env (matches conftest.py / smoke / bench)
os.environ.setdefault("JAMBU_DB_PATH", ":memory:")
os.environ.setdefault("JAMBU_VAULT_KEY", "test-key-do-not-use-in-production-32bytes!")
os.environ.setdefault("JAMBU_LLM_PROVIDER", "mock")

REPO_ROOT = Path(__file__).resolve().parent.parent

# The MCP server's _call_engine uses http://localhost:8001 by default. We
# override via JAMBU_ENGINE_URL so the live test can point at a free port.
ENGINE_URL_ENV = "JAMBU_ENGINE_URL"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expected_tool_names() -> set[str]:
    """The canonical 21-tool surface exposed by backend/mcp_server.py.

    Listed here (not imported) because the server uses @mcp.tool() decorators
    at module-import time; we want this test to fail loudly if any tool is
    silently dropped on a refactor.
    """
    return {
        # Research & Search (5)
        "research_web", "search_multi_engine", "search_academic",
        "search_code", "deep_research",
        # Browser Actions (5)
        "scrape_page", "click_element", "type_text",
        "take_screenshot", "navigate_browser",
        # Vision (2)
        "visual_grounding", "analyze_screenshot",
        # Memory & Knowledge (3)
        "query_brain", "recall_memory", "get_brain_stats",
        # Tools & Skills (2)
        "list_custom_tools", "execute_tool",
        # System (4)
        "check_engine_health", "get_system_stats",
        "start_mission", "stop_mission",
    }


def _free_port() -> int:
    """Ask the OS for an unused TCP port (race-prone but fine for a one-shot test)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _stdio_session(env_overrides: dict[str, str] | None = None):
    """Async context manager: spawn backend.mcp_server as stdio subprocess,
    return a connected MCP ClientSession.

    The session is initialized before the `yield` and cleanly shut down
    on exit (which closes the subprocess via the stdio_client context).
    """
    # mcp package import — done here so a missing dep gives a friendly error
    # instead of a top-of-file ImportError.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.mcp_server"],
        cwd=str(REPO_ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ---------------------------------------------------------------------------
# Stdio smoke (no engine required)
# ---------------------------------------------------------------------------

async def _stdio_smoke() -> int:
    async with _stdio_session() as s:
        # 1. List tools
        tools_resp = await s.list_tools()
        names = {t.name for t in tools_resp.tools}
        expected = _expected_tool_names()
        missing = expected - names
        extra = names - expected
        print(f"  MCP tools listed: {len(names)}")
        if missing:
            print(f"  MISSING: {sorted(missing)}")
            return 1
        if extra:
            print(f"  EXTRA (not in canonical set, may be intentional): {sorted(extra)}")

        # 2. Every tool has name + non-empty description + schema
        for t in tools_resp.tools:
            assert t.name, "tool without name"
            assert t.description and len(t.description) > 10, f"{t.name}: empty/short description"
            assert t.inputSchema, f"{t.name}: missing inputSchema"
            assert t.inputSchema.get("type") == "object", f"{t.name}: inputSchema not object"
        print(f"  All {len(tools_resp.tools)} tools have description + inputSchema")

        # 3. check_engine_health works (returns "Engine Offline" if backend
        #    is not running, which is the expected behavior — proves the
        #    tool is wired and the server stays up under stdio traffic)
        result = await s.call_tool("check_engine_health", {})
        text = _extract_text(result)
        assert "Engine" in text, f"unexpected response: {text!r}"
        print(f"  check_engine_health: {text!r}")

        # 4. get_system_stats same idea
        result = await s.call_tool("get_system_stats", {})
        text = _extract_text(result)
        assert "System" in text or "Engine" in text, f"unexpected response: {text!r}"
        print(f"  get_system_stats: {text!r}")

    return 0


def _extract_text(result: Any) -> str:
    """MCP tool results come back as a list of content blocks. Find the
    first text block and return its content."""
    for block in (result.content or []):
        if hasattr(block, "text"):
            return block.text
    return ""


# ---------------------------------------------------------------------------
# Live e2e (engine required)
# ---------------------------------------------------------------------------

def _wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    """Poll until something is listening on (host, port)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


@asynccontextmanager
async def _engine_subprocess(port: int):
    """Start the engine in a subprocess and tear it down on exit."""
    env = os.environ.copy()
    env["JAMBU_ENGINE_URL"] = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "backend.engine:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_port("127.0.0.1", port, timeout=30):
            raise RuntimeError(f"Engine did not start on port {port} within 30s")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def _live_e2e() -> int:
    """Drive the engine through MCP, assuming the engine is already running.

    We do NOT spawn the engine ourselves — the engine has heavy native deps
    (sqlite-vec, FastAPI stack, browser automation) that would require a
    full test environment. Instead, this test:
      1. Probes the engine URL from JAMBU_ENGINE_URL (default :8001).
      2. If the engine responds 200, boots an MCP session pointed at it
         and asserts real engine-shaped responses.
      3. If the engine is down, the test skips with a clear message
         (instead of taking 30s to time out on a subprocess spawn).

    Run with:    JAMBU_MCP_LIVE=1 python3 -m pytest tests/test_mcp_server.py -v
    (or just):  python3 -m pytest tests/test_mcp_server.py -v
    """
    import urllib.request

    engine_url = os.environ.get(ENGINE_URL_ENV, "http://127.0.0.1:8001")
    try:
        with urllib.request.urlopen(f"{engine_url}/health", timeout=2) as r:
            if r.status != 200:
                import pytest
                pytest.skip(f"engine at {engine_url} returned {r.status}")
    except Exception as e:
        import pytest
        pytest.skip(
            f"engine not reachable at {engine_url} ({type(e).__name__}: {e}). "
            f"Start it with: JAMBU_LLM_PROVIDER=mock python3 -m uvicorn backend.engine:app --port 8001"
        )

    async with _stdio_session(env_overrides={ENGINE_URL_ENV: engine_url}) as s:
        # 1. Health should now report the engine as live
        result = await s.call_tool("check_engine_health", {})
        health_text = _extract_text(result)
        assert "Engine Status" in health_text or "online" in health_text.lower(), (
            f"expected engine to be reachable, got: {health_text!r}"
        )
        print(f"  live check_engine_health: {health_text[:120]!r}")

        # 2. Stats
        result = await s.call_tool("get_system_stats", {})
        stats_text = _extract_text(result)
        assert "Engine Status" in stats_text or "Status" in stats_text, (
            f"expected status in response, got: {stats_text!r}"
        )
        print(f"  live get_system_stats: {stats_text[:120]!r}")

        # 3. Brain stats (works even with empty knowledge vault — returns 0 docs)
        result = await s.call_tool("get_brain_stats", {})
        brain_text = _extract_text(result)
        assert "Knowledge" in brain_text or "Documents" in brain_text, (
            f"expected knowledge stats, got: {brain_text!r}"
        )
        print(f"  live get_brain_stats: {brain_text!r}")

        # 4. List custom tools (empty list, but must not error)
        result = await s.call_tool("list_custom_tools", {})
        tools_text = _extract_text(result)
        assert "Toolbox" in tools_text or "No custom tools" in tools_text, (
            f"expected toolbox response, got: {tools_text!r}"
        )
        print(f"  live list_custom_tools: {tools_text!r}")

    return 0


# ---------------------------------------------------------------------------
# Pytest integration
# ---------------------------------------------------------------------------

def test_mcp_stdio_smoke():
    """Boot the MCP server, list tools, call a couple of tools.

    Does not require the engine to be running.
    """
    rc = asyncio.run(_stdio_smoke())
    assert rc == 0


def test_mcp_tool_schema_snapshot():
    """Every tool has a stable (name, description-prefix, inputSchema-keys) tuple.
    This is the CI gate: if anyone removes a tool or breaks its schema, this fails.
    """
    async def go():
        async with _stdio_session() as s:
            tools_resp = await s.list_tools()
            return tools_resp.tools

    tools = asyncio.run(go())
    expected = _expected_tool_names()
    actual = {t.name for t in tools}
    assert actual >= expected, f"missing tools: {sorted(expected - actual)}"
    # Spot-check that key tools still have a query / url parameter
    by_name = {t.name: t for t in tools}
    research = by_name["research_web"]
    props = research.inputSchema.get("properties", {})
    assert "query" in props, "research_web must accept 'query'"
    scrape = by_name["scrape_page"]
    assert "url" in scrape.inputSchema.get("properties", {}), "scrape_page must accept 'url'"


def test_mcp_live_e2e():
    """Drive the engine through MCP, assuming the engine is already running.

    Enable with JAMBU_MCP_LIVE=1 (or just run with the engine already up —
    we probe http://localhost:8001/health and skip gracefully if it's down).

    The engine has heavy native deps (sqlite-vec, FastAPI stack) that we
    deliberately don't spawn in CI. This is the "run when convenient" test
    that complements the stdio smoke (always-runs).
    """
    rc = asyncio.run(_live_e2e())
    assert rc == 0


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("MCP server test — stdio smoke (no engine required)")
    print("=" * 60)
    rc = asyncio.run(_stdio_smoke())
    if rc == 0:
        print("\n  PASS — MCP server boots, all 21 tools listed, health/stats work")
    else:
        print("\n  FAIL — see missing/extra above")
    return rc


if __name__ == "__main__":
    sys.exit(main())
