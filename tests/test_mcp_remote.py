"""
Remote MCP tests — Streamable HTTP transport, token auth, Server Card,
tool profiles, and the engine mount.

The protocol E2E spawns the standalone app on a free port and connects with
the official MCP client (streamablehttp_client), so transport + auth +
tool listing are exercised end to end. Auth boundary tests use ASGI
transport (no subprocess).
"""
from __future__ import annotations

import asyncio
import importlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh_session_manager():
    """Each app in this module gets a fresh MCP session manager.

    The SDK's StreamableHTTPSessionManager can only be run once per
    instance; without this, the second TestClient in one process raises
    'can only be called once per instance'.
    """
    from backend.mcp_http import reset_session_manager

    reset_session_manager()
    yield
    reset_session_manager()


# ---------------------------------------------------------------------------
# Unit: card, auth, profile
# ---------------------------------------------------------------------------

class TestServerCard:
    def test_card_shape_and_no_secrets(self, monkeypatch):
        monkeypatch.setenv("JAMBU_MCP_TOKEN", "super-secret-token")
        monkeypatch.setenv("JAMBU_MCP_PUBLIC_URL", "https://mcp.example.com")
        from backend.mcp_http import server_card

        card = server_card()
        assert card["name"].count("/") == 1  # reverse-DNS format
        assert card["name"].startswith("io.github.")
        assert len(card["description"]) <= 100
        transports = {t["type"] for t in card["transports"]}
        assert "streamable-http" in transports
        assert "stdio" in transports
        assert card["authentication"]["type"] == "bearer"
        assert card["tools"]["count"] == 37
        assert card["transports"][0]["url"] == "https://mcp.example.com/mcp/"
        # The card is public: no token material may leak.
        assert "super-secret-token" not in str(card)

    def test_card_url_falls_back_to_localhost(self, monkeypatch):
        monkeypatch.delenv("JAMBU_MCP_PUBLIC_URL", raising=False)
        from backend.mcp_http import server_card
        assert server_card()["transports"][0]["url"] == "http://127.0.0.1:8001/mcp/"


class TestTokenCheck:
    def test_static_token(self, monkeypatch):
        monkeypatch.setenv("JAMBU_MCP_TOKEN", "static-token")
        from backend.mcp_http import check_token
        assert check_token("static-token") is True
        assert check_token("wrong") is False
        assert check_token(None) is False

    def test_api_key_from_db(self, monkeypatch):
        monkeypatch.delenv("JAMBU_MCP_TOKEN", raising=False)
        from backend.core.api_keys import create_api_key
        from backend.mcp_http import check_token

        raw_key, _ = create_api_key(name="remote-mcp-test")
        assert check_token(raw_key) is True
        assert check_token("jambu_not-a-real-key") is False


class TestAuthBoundary:
    """Auth is enforced by a raw ASGI middleware; requests that pass it need
    the MCP session manager, which each app runs via its lifespan — so these
    tests use TestClient (runs lifespan) rather than a bare ASGITransport."""

    def _client(self, monkeypatch, token: str = "tok"):
        monkeypatch.setenv("JAMBU_MCP_TOKEN", token)
        from starlette.testclient import TestClient
        from backend.mcp_http import build_app

        return TestClient(build_app())

    @staticmethod
    def _initialize(client, headers=None):
        return client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                **(headers or {}),
            },
        )

    def test_missing_token_is_401_with_challenge(self, monkeypatch):
        with self._client(monkeypatch) as client:
            resp = self._initialize(client)
            assert resp.status_code == 401
            assert "Bearer" in resp.headers.get("www-authenticate", "")
            assert "Authorization: Bearer" in resp.json()["detail"]

    def test_wrong_token_is_401(self, monkeypatch):
        with self._client(monkeypatch) as client:
            resp = self._initialize(
                client, {"Authorization": "Bearer nope"},
            )
            assert resp.status_code == 401

    def test_valid_token_passes_the_gate(self, monkeypatch):
        with self._client(monkeypatch) as client:
            resp = self._initialize(
                client, {"Authorization": "Bearer tok"},
            )
            assert resp.status_code != 401

    def test_x_api_key_header_also_works(self, monkeypatch):
        with self._client(monkeypatch) as client:
            resp = self._initialize(client, {"X-API-Key": "tok"})
            assert resp.status_code != 401

    def test_health_and_card_are_public(self, monkeypatch):
        with self._client(monkeypatch) as client:
            health = client.get("/health")
            card = client.get("/.well-known/mcp-server-card.json")
        assert health.status_code == 200
        assert card.status_code == 200
        assert card.json()["tools"]["count"] == 37


class TestToolProfile:
    def _reload(self, profile: str):
        os.environ["JAMBU_MCP_PROFILE"] = profile
        import backend.mcp_server as mcp_mod
        importlib.reload(mcp_mod)
        return mcp_mod

    def test_curated_removes_execute_tool_and_full_restores(self):
        try:
            curated = self._reload("curated")
            names = {t.name for t in curated.mcp._tool_manager.list_tools()}
            assert "execute_tool" not in names
            assert "meshpay_audit" in names  # everything else stays
            assert "browser_session_open" in names
            assert len(names) == 36
        finally:
            full = self._reload("full")
            names = {t.name for t in full.mcp._tool_manager.list_tools()}
            assert "execute_tool" in names
            assert len(names) == 37
            os.environ.pop("JAMBU_MCP_PROFILE", None)


class TestRegistryManifest:
    """server.json must stay schema-shaped for the official MCP registry."""

    def _manifest(self) -> dict:
        import json

        return json.loads((REPO_ROOT / "server.json").read_text())

    def test_required_fields_and_patterns(self):
        m = self._manifest()
        assert m["name"].count("/") == 1
        import re
        assert re.match(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$", m["name"])
        assert 1 <= len(m["description"]) <= 100
        assert m["version"] not in ("latest",) and not m["version"][0] in "^~"
        assert m["repository"]["source"] == "github"

    def test_remote_transport_shape(self):
        m = self._manifest()
        remote = m["remotes"][0]
        assert remote["type"] == "streamable-http"
        import re
        assert re.match(r"^(\{baseUrl\}|https?://)", remote["url"])
        headers = {h["name"] for h in remote["headers"]}
        assert "Authorization" in headers
        assert remote["variables"]["baseUrl"]["isRequired"] is True


# ---------------------------------------------------------------------------
# Engine mount
# ---------------------------------------------------------------------------

class TestEngineMount:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from backend.engine import app
        with TestClient(app) as c:
            yield c

    def test_card_served_by_engine(self, client):
        resp = client.get("/.well-known/mcp-server-card.json")
        assert resp.status_code == 200
        assert resp.json()["tools"]["count"] == 37

    def test_mcp_mount_requires_auth(self, client, monkeypatch):
        monkeypatch.setenv("JAMBU_MCP_TOKEN", "engine-tok")
        resp = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Protocol E2E with the official client
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestProtocolE2E:
    @pytest.fixture(scope="class")
    def server(self):
        port = _free_port()
        env = {
            **os.environ,
            "JAMBU_MCP_TOKEN": "e2e-token",
            "JAMBU_MCP_PROFILE": "full",
            "JAMBU_DB_PATH": ":memory:",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.mcp_http:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=str(REPO_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            proc.terminate()
            raise RuntimeError("mcp_http server did not start")
        yield base
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def test_official_client_lists_tools_with_token(self, server):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def go():
            async with streamablehttp_client(
                f"{server}/mcp/",
                headers={"Authorization": "Bearer e2e-token"},
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    return init, tools

        init, tools = asyncio.run(go())
        assert init.serverInfo.name.startswith("Jambubrowser")
        names = {t.name for t in tools.tools}
        assert len(names) == 37
        assert {"research_web", "dcm_infer", "meshpay_audit", "browser_session_act",
                "browser_test_flow"} <= names

    def test_official_client_without_token_fails(self, server):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def go():
            async with streamablehttp_client(f"{server}/mcp/") as (read, write, _):
                # The 401 surfaces when the session actually talks.
                async with ClientSession(read, write) as session:
                    await session.initialize()

        with pytest.raises(Exception):
            asyncio.run(go())
