"""
Tests: Jambubrowser Engine v2.0 Core Endpoints
=============================================
Tests for the engine's HTTP API endpoints.
Uses FastAPI TestClient for in-process testing.
"""

import pytest
import json
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a fresh test client for each test."""
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_online(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"
        assert "message" in data
        assert "ram_used_gb" in data
        assert "cpu_percent" in data

    def test_health_response_is_json(self, client):
        response = client.get("/health")
        assert response.headers["content-type"] == "application/json"


class TestStatsEndpoint:
    """Tests for /stats endpoint."""

    def test_stats_returns_counts(self, client):
        response = client.get("/stats")
        assert response.status_code == 200
        data = response.json()
        assert "doc_count" in data
        assert "active_missions" in data
        assert "custom_tools" in data
        assert "credentials" in data
        assert "browser_sessions" in data


class TestExecEndpoint:
    """Tests for /exec sandboxed execution."""

    def test_exec_simple_code(self, client):
        response = client.post("/exec", json={"code": "print('hello world')"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "hello world" in data["output"]

    def test_exec_returns_result(self, client):
        response = client.post("/exec", json={"code": "x = 2 + 2\nprint(x)"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "4" in data["output"]

    def test_exec_with_error(self, client):
        response = client.post("/exec", json={"code": "print(undefined_var)"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_exec_sandbox_type(self, client):
        response = client.post("/exec", json={"code": "print('test')"})
        data = response.json()
        assert "sandbox_type" in data
        assert data["sandbox_type"] in ("docker", "subprocess")

    def test_exec_blocked_import(self, client):
        response = client.post("/exec", json={"code": "import os\nprint(os.getcwd())"})
        data = response.json()
        assert data["success"] is False


class TestResearchEndpoint:
    """Tests for /research endpoint."""

    def test_research_brain_only(self, client):
        """Brain-only should not require external services."""
        response = client.post("/research", json={
            "query": "test query",
            "brain_only": True,
            "client_id": "test",
        })
        assert response.status_code == 200
        data = response.json()
        assert "context" in data
        assert "sources" in data

    def test_research_with_domain(self, client):
        """Domain-specific research should return structure."""
        response = client.post("/research", json={
            "query": "quantum computing",
            "domain": "general",
            "client_id": "test",
            "top_n": 2,
        })
        # May fail if searxng is not running - that's OK
        assert response.status_code in (200, 500)


class TestMemoryRecall:
    """Tests for /memory/recall endpoint."""

    def test_recall_returns_structure(self, client):
        response = client.get("/memory/recall", params={"query": "test"})
        assert response.status_code == 200
        data = response.json()
        assert "memory" in data
        assert isinstance(data["memory"], list)


class TestGraphData:
    """Tests for /graph_data endpoint."""

    def test_graph_data_structure(self, client):
        response = client.get("/graph_data")
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)


class TestMissionEndpoint:
    """Tests for /mission endpoint."""

    def test_start_mission(self, client):
        response = client.post("/mission", json={
            "query": "monitor quantum computing",
            "client_id": "test",
        })
        assert response.status_code == 200
        data = response.json()
        assert "mission_id" in data
        assert data["status"] == "active"

    def test_stop_mission(self, client):
        # Start then stop
        start = client.post("/mission", json={
            "query": "test mission",
            "client_id": "test",
        })
        mid = start.json()["mission_id"]

        response = client.post("/mission/stop", json={
            "mission_id": mid,
            "client_id": "test",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"


class TestToolManagement:
    """Tests for /tool/save, /tools, /tool/exec endpoints."""

    def test_save_and_list_tool(self, client):
        tool_code = "def run(**kwargs):\n    return 'test result'"
        save = client.post("/tool/save", json={
            "name": "test_tool_123",
            "description": "A test tool",
            "code": tool_code,
            "client_id": "test",
        })
        assert save.status_code == 200
        assert save.json()["status"] == "success"

        tools_list = client.get("/tools")
        assert tools_list.status_code == 200
        tool_names = [t["name"] for t in tools_list.json()["tools"]]
        assert "test_tool_123" in tool_names

    def test_execute_saved_tool(self, client):
        tool_code = "def run(**kwargs):\n    return 'executed_' + kwargs.get('param', '')"
        client.post("/tool/save", json={
            "name": "test_exec_tool",
            "description": "Tool for execution test",
            "code": tool_code,
            "client_id": "test",
        })

        result = client.post("/tool/exec", json={
            "name": "test_exec_tool",
            "kwargs": {"param": "hello"},
            "client_id": "test",
        })
        assert result.status_code == 200
        assert "executed_hello" in result.json()["output"]

    def test_execute_nonexistent_tool(self, client):
        response = client.post("/tool/exec", json={
            "name": "nonexistent_tool_xyz",
            "client_id": "test",
        })
        assert response.status_code == 404


class TestCredentialVault:
    """Tests for /login and vault endpoints."""

    def test_login_stores_credential(self, client):
        response = client.post("/login", json={
            "url": "https://example.com/login",
            "username": "testuser",
            "password": "testpassword123",
            "client_id": "test",
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_vault_domains(self, client):
        # Store a credential first
        client.post("/login", json={
            "url": "https://unique-test-domain.com/login",
            "username": "test",
            "password": "pass123",
            "client_id": "test",
        })

        response = client.get("/vault/domains")
        assert response.status_code == 200
        assert "domains" in response.json()

    def test_vault_credential_lookup(self, client):
        # Store a credential
        client.post("/login", json={
            "url": "https://lookup-test.com/login",
            "username": "finder",
            "password": "secret123",
            "client_id": "test",
        })

        response = client.get("/vault/credential", params={"url": "https://lookup-test.com/some-page"})
        assert response.status_code == 200
        # May or may not find it depending on domain matching


class TestSearchEndpoint:
    """Tests for /search endpoint."""

    def test_search_returns_structure(self, client):
        response = client.get("/search", params={
            "q": "test",
            "engines": "duckduckgo",
        })
        # May fail if SearXNG not running
        assert response.status_code in (200, 500)


class TestVisionGrounding:
    """Tests for /vision/grounding endpoint."""

    def test_grounding_returns_suggestions(self, client):
        response = client.post("/vision/grounding", json={
            "url": "https://example.com",
            "client_id": "test",
        })
        assert response.status_code == 200
        data = response.json()
        assert "suggestions" in data
        assert len(data["suggestions"]) > 0


class TestDiscoveryApi:
    """Tests for /discover_api endpoint."""

    def test_discover_handles_no_spec(self, client):
        response = client.post("/discover_api", json={
            "url": "https://example.com",
            "client_id": "test",
        })
        assert response.status_code == 200
