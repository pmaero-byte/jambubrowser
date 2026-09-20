"""
Tests for the /dcm/* engine routes and the DCM MCP tools.

Routes are tested with a stubbed DcmClient / provider so the suite never
needs a live node; the live contract is covered separately by
tests/test_dcm_integration.py.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.dcm_client import DcmError


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class FakeDcmClient:
    """Stands in for DcmClient with recorded calls."""

    def __init__(self, *, error: DcmError | None = None, payloads: dict | None = None):
        self.error = error
        self.payloads = payloads or {}
        self.calls: list[tuple] = []

    def _result(self, key: str, default: Any):
        self.calls.append((key,))
        if self.error:
            raise self.error
        return self.payloads.get(key, default)

    async def summary(self):
        return self._result("summary", {"reachable": True, "models": []})

    async def models(self):
        return self._result("models", [{"id": "qwen1.5-moe-a2.7b", "status": "ready"}])

    async def join_info(self):
        return self._result("join_info", {"hosts": ["127.0.0.1"], "ports": {"peerPage": 3002}})

    async def earnings(self, did):
        self.calls.append(("earnings", did))
        if self.error:
            raise self.error
        return {"did": did, "pendingDct": 3.5}

    async def token_balance(self, did):
        self.calls.append(("token_balance", did))
        if self.error:
            raise self.error
        return {"did": did, "balance": 42.0}

    async def settlement_log(self, limit=50):
        self.calls.append(("settlement_log", limit))
        if self.error:
            raise self.error
        return {"entries": [], "valid": True, "limit": limit}

    async def simulate(self, problem_id, dofs, partitions=1, iterations=None):
        self.calls.append(("simulate", problem_id, dofs, partitions, iterations))
        if self.error:
            raise self.error
        return self.payloads.get("simulate", {"success": True, "simulationId": "sim-x"})


def _install_client(monkeypatch, fake: FakeDcmClient):
    import backend.routes.dcm as dcm_routes
    monkeypatch.setattr(dcm_routes, "_client", lambda: fake)


class TestDcmRoutes:
    def test_status(self, client, monkeypatch):
        fake = FakeDcmClient(payloads={"summary": {"reachable": True, "models": [{"id": "m"}]}})
        _install_client(monkeypatch, fake)
        resp = client.get("/dcm/status")
        assert resp.status_code == 200
        assert resp.json()["reachable"] is True

    def test_models_returns_count(self, client, monkeypatch):
        fake = FakeDcmClient()
        _install_client(monkeypatch, fake)
        resp = client.get("/dcm/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["models"][0]["id"] == "qwen1.5-moe-a2.7b"

    def test_join_info(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient())
        resp = client.get("/dcm/join-info")
        assert resp.status_code == 200
        assert resp.json()["ports"]["peerPage"] == 3002

    def test_earnings(self, client, monkeypatch):
        fake = FakeDcmClient()
        _install_client(monkeypatch, fake)
        resp = client.get("/dcm/earnings/did:dcm:abc")
        assert resp.status_code == 200
        assert resp.json()["pendingDct"] == 3.5
        assert ("earnings", "did:dcm:abc") in fake.calls

    def test_token_balance(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient())
        resp = client.get("/dcm/token/balance/did:dcm:abc")
        assert resp.status_code == 200
        assert resp.json()["balance"] == 42.0

    def test_settlement_log_limit_passthrough(self, client, monkeypatch):
        fake = FakeDcmClient()
        _install_client(monkeypatch, fake)
        resp = client.get("/dcm/settlement-log?limit=5")
        assert resp.status_code == 200
        assert ("settlement_log", 5) in fake.calls

    def test_settlement_log_limit_validated(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient())
        assert client.get("/dcm/settlement-log?limit=0").status_code == 422
        assert client.get("/dcm/settlement-log?limit=501").status_code == 422

    def test_unreachable_node_gives_actionable_502(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient(error=DcmError(0, "unreachable")))
        resp = client.get("/dcm/status")
        assert resp.status_code == 502
        assert "decentracode/backend" in resp.json()["detail"]

    def test_node_error_surfaces_detail(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient(error=DcmError(500, "boom")))
        resp = client.get("/dcm/models")
        assert resp.status_code == 502
        assert "boom" in resp.json()["detail"]


class TestDcmInferRoute:
    def _install_provider(self, monkeypatch, provider):
        import backend.llm.registry as registry_mod

        class StubRegistry:
            def get(self, name):
                assert name == "dcm"
                return provider

        monkeypatch.setattr(registry_mod, "get_registry", lambda: StubRegistry())

    def test_infer_returns_content_and_usage(self, client, monkeypatch):
        from backend.llm.base import ChatResponse, Usage

        class FakeProvider:
            async def chat(self, messages, *, model=None, max_tokens=64, **kw):
                assert messages[0].content == "hello mesh"
                assert max_tokens == 8
                return ChatResponse(
                    content="hi from the mesh",
                    model="qwen1.5-moe-a2.7b",
                    provider="dcm",
                    usage=Usage(prompt_tokens=2, completion_tokens=4, total_tokens=6),
                    latency_ms=123.4,
                )

        self._install_provider(monkeypatch, FakeProvider())
        resp = client.post("/dcm/infer", json={"prompt": "hello mesh", "max_tokens": 8})
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "hi from the mesh"
        assert body["usage"]["completion_tokens"] == 4

    def test_infer_validates_prompt_and_max_tokens(self, client, monkeypatch):
        self._install_provider(monkeypatch, None)
        assert client.post("/dcm/infer", json={"prompt": "   "}).status_code == 422
        assert client.post(
            "/dcm/infer", json={"prompt": "x", "max_tokens": 0},
        ).status_code == 422

    def test_runtime_missing_maps_to_502_with_hint(self, client, monkeypatch):
        from backend.llm.base import ProviderUnavailable

        class FakeProvider:
            async def chat(self, messages, **kw):
                raise ProviderUnavailable(
                    "DCM runtime not available for qwen2-0.5b-instruct: "
                    "spawn ...dcm-infer-candle ENOENT (build backend/p2pd binaries "
                    "or start the dense coordinator)"
                )

        self._install_provider(monkeypatch, FakeProvider())
        resp = client.post("/dcm/infer", json={"prompt": "hi"})
        assert resp.status_code == 502
        assert "build backend/p2pd binaries" in resp.json()["detail"]

    def test_auth_error_maps_to_502_with_env_hint(self, client, monkeypatch):
        from backend.llm.base import ProviderAuthError

        class FakeProvider:
            async def chat(self, messages, **kw):
                raise ProviderAuthError("DCM auth failed (401)")

        self._install_provider(monkeypatch, FakeProvider())
        resp = client.post("/dcm/infer", json={"prompt": "hi"})
        assert resp.status_code == 502
        assert "JAMBU_LLM_DCM_AUTH" in resp.json()["detail"]


class TestDcmMcpTools:
    def _tool_names(self) -> set[str]:
        """List tools without stdio: read the FastMCP registry directly."""
        async def go():
            tools = await __import__(
                "backend.mcp_server", fromlist=["mcp"],
            ).mcp.list_tools()
            return {t.name for t in tools}

        return asyncio.run(go())

    def test_dcm_tools_are_registered(self):
        names = self._tool_names()
        for tool in (
            "dcm_status", "dcm_infer", "dcm_models",
            "dcm_earnings", "dcm_settlement_log",
        ):
            assert tool in names, f"{tool} missing from MCP surface"

    def test_dcm_tools_have_descriptions_and_schemas(self):
        async def go():
            tools = await __import__(
                "backend.mcp_server", fromlist=["mcp"],
            ).mcp.list_tools()
            return {t.name: t for t in tools}

        tools = asyncio.run(go())
        for name in ("dcm_status", "dcm_infer", "dcm_models",
                     "dcm_earnings", "dcm_settlement_log"):
            tool = tools[name]
            assert tool.description and len(tool.description) > 20
            assert isinstance(tool.inputSchema, dict)

        props = tools["dcm_infer"].inputSchema.get("properties", {})
        assert "prompt" in props
        assert "model" in props


class TestDcmMcpFormatting:
    """Formatter regressions found by calling the tools against a real node."""

    def test_status_shows_moe_ready_when_default_runtime_errors(self, monkeypatch):
        import backend.mcp_server as mcp_server

        async def fake_call(method, path, json_data=None, timeout=60.0):
            return {
                "base_url": "http://127.0.0.1:3001",
                "reachable": True,
                "inference_status": {
                    "runtime": "candle-dense", "ready": False,
                    "error": "Binary not found: /x/dcm-infer-candle",
                    "moe": {"runtime": "python-moe", "ready": True, "available": True},
                },
                "models": [
                    {"id": "qwen1.5-moe-a2.7b", "status": "ready"},
                    {"id": "glm-5.2", "status": "planned"},
                ],
                "mesh_status": {"nodeId": "n1", "peers": []},
            }

        monkeypatch.setattr(mcp_server, "_call_engine", fake_call)
        out = asyncio.run(mcp_server.dcm_status())
        assert "python-moe ready" in out
        assert "dcm-infer-candle" in out
        assert "1/2 available" in out

    def test_settlement_log_reads_nested_verification(self, monkeypatch):
        import backend.mcp_server as mcp_server

        async def fake_call(method, path, json_data=None, timeout=60.0):
            return {
                "success": True,
                "count": 0,
                "entries": [],
                "verification": {
                    "valid": True, "entries": 0, "brokenAt": None,
                    "totals": {"totalMinted": 0, "totalCharged": 0},
                },
            }

        monkeypatch.setattr(mcp_server, "_call_engine", fake_call)
        out = asyncio.run(mcp_server.dcm_settlement_log(3))
        assert "chain valid: True" in out
        assert "totalMinted=0" in out

    def test_call_engine_surfaces_non_200_detail(self, monkeypatch):
        """Engine 502s must carry their detail (e.g. DCM's missing-runtime
        explanation) instead of a bare status code."""
        import backend.mcp_server as mcp_server

        class FakeResponse:
            status_code = 502

            def json(self):
                return {"detail": "DCM 500: dcm-infer-candle ENOENT"}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return FakeResponse()

            async def get(self, *a, **k):
                return FakeResponse()

        monkeypatch.setattr(mcp_server.httpx, "AsyncClient", FakeClient)
        result = asyncio.run(
            mcp_server._call_engine("POST", "/dcm/infer", {"prompt": "hi"})
        )
        assert "Engine HTTP 502" in result["error"]
        assert "dcm-infer-candle ENOENT" in result["error"]


class TestDcmSimulateRoute:
    """POST /dcm/simulate — distributed-solve dispatch for external apps
    (e.g. CFD Lab's Debug-stage "Run on mesh")."""

    def test_simulate_returns_result_and_billing(self, client, monkeypatch):
        fake = FakeDcmClient(
            payloads={
                "simulate": {
                    "success": True,
                    "simulationId": "sim-abc",
                    "result": {
                        "iterations": 65,
                        "finalResidual": 9.5e-11,
                        "subdomains": 2,
                        "proofLedger": [],
                        "elapsedMs": 812,
                    },
                    "billing": {"chargedDct": 0.0001328, "balanceAfter": 99.9},
                }
            }
        )
        _install_client(monkeypatch, fake)
        resp = client.post(
            "/dcm/simulate",
            json={"problem_id": "poisson-fvm-2d", "dofs": 4096, "partitions": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["simulationId"] == "sim-abc"
        assert body["billing"]["chargedDct"] == pytest.approx(0.0001328)
        assert ("simulate", "poisson-fvm-2d", 4096, 2, None) in fake.calls

    def test_simulate_iterations_passthrough(self, client, monkeypatch):
        fake = FakeDcmClient(payloads={"simulate": {"success": True, "simulationId": "s"}})
        _install_client(monkeypatch, fake)
        resp = client.post(
            "/dcm/simulate",
            json={"problem_id": "navier-stokes-2d", "dofs": 65536, "iterations": 500},
        )
        assert resp.status_code == 200
        assert ("simulate", "navier-stokes-2d", 65536, 1, 500) in fake.calls

    def test_insufficient_balance_is_passed_through_as_402(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient(error=DcmError(402, "Insufficient balance: have 0.001")))
        resp = client.post("/dcm/simulate", json={"problem_id": "poisson-fvm-2d", "dofs": 64})
        assert resp.status_code == 402
        assert "Insufficient balance" in resp.json()["detail"]

    def test_unreachable_node_gives_actionable_502(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient(error=DcmError(0, "unreachable")))
        resp = client.post("/dcm/simulate", json={"problem_id": "poisson-fvm-2d", "dofs": 64})
        assert resp.status_code == 502
        assert "decentracode/backend" in resp.json()["detail"]

    def test_node_error_surfaces_detail(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient(error=DcmError(501, "runtime not wired")))
        resp = client.post("/dcm/simulate", json={"problem_id": "navier-stokes-2d", "dofs": 64})
        assert resp.status_code == 502
        assert "runtime not wired" in resp.json()["detail"]

    def test_request_validation(self, client, monkeypatch):
        _install_client(monkeypatch, FakeDcmClient())
        assert client.post("/dcm/simulate", json={"problem_id": "", "dofs": 64}).status_code == 422
        assert client.post("/dcm/simulate", json={"problem_id": "poisson-fvm-2d", "dofs": 0}).status_code == 422
        assert client.post("/dcm/simulate", json={"problem_id": "poisson-fvm-2d", "dofs": 64, "partitions": 0}).status_code == 422
        assert client.post("/dcm/simulate", json={"problem_id": "p", "dofs": 64, "iterations": 0}).status_code == 422
