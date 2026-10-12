"""Greenwich Studio bridge — regression tests for the 7-item spec."""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from backend.llm import reload_config
    from backend.llm.registry import reset_registry
    reload_config()
    reset_registry()
    yield
    reset_registry()


class TestGreenwichProvider:
    def test_discovered(self):
        from backend.llm.registry import get_registry
        assert "greenwich" in get_registry().list_available()
    def test_config_defaults(self):
        from backend.llm.config import LLMConfig
        c = LLMConfig.from_env()
        assert c.greenwich_base_url == "http://127.0.0.1:8085/v1"
        assert c.greenwich_extended_url == "http://127.0.0.1:8090/v1"
        assert c.greenwich_model == "qwen3.8-27b"
        assert c.model_for("greenwich") == "qwen3.8-27b"
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("GREENWICH_BASE_URL", "http://127.0.0.1:9999/v1")
        monkeypatch.setenv("GREENWICH_MODEL", "custom-model")
        from backend.llm.config import LLMConfig
        c = LLMConfig.from_env()
        assert c.greenwich_base_url == "http://127.0.0.1:9999/v1"
        assert c.greenwich_model == "custom-model"
    def test_thinking_stripped_by_default(self):
        from backend.llm.config import LLMConfig
        from backend.llm.providers.greenwich import GreenwichProvider
        p = GreenwichProvider(LLMConfig.from_env())
        assert p.enable_thinking is False
        assert p._normalize("<think>private</think> {\"a\": 1}") == "{\"a\": 1}"
    def test_local_zero_cost(self):
        from backend.llm.base import Usage, estimate_cost_for_model
        u = Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        assert estimate_cost_for_model("greenwich", "qwen3.8-27b", u) == 0.0
    def test_routing_treats_greenwich_as_local(self):
        from backend.llm.routing import Router
        assert "greenwich" in Router.LOCAL_PROVIDERS


class TestLocalWorkspaces:
    def test_localhost_blocked_by_default(self):
        from backend.core.security import is_safe_url
        assert is_safe_url("http://127.0.0.1:8085/v1") is False
        assert is_safe_url("http://localhost:3000/") is False
    def test_localhost_allowed_with_flag(self, monkeypatch):
        monkeypatch.setenv("ALLOW_LOCAL_WORKSPACES", "true")
        from backend.core.security import is_safe_url
        assert is_safe_url("http://127.0.0.1:8085/v1") is True
        assert is_safe_url("http://localhost:3000/") is True
        assert is_safe_url("http://192.168.1.10/") is False
    def test_file_url_confined(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOW_LOCAL_WORKSPACES", "true")
        monkeypatch.setenv("JAMBU_LOCAL_WORKSPACE_ROOTS", str(tmp_path))
        from backend.core.security import is_safe_file_url
        inside = (tmp_path / "doc.html")
        inside.write_text("<h1>hi</h1>")
        assert is_safe_file_url(inside.as_uri()) is True
        assert is_safe_file_url("file:///etc/passwd") is False
    def test_file_url_disabled_by_default(self):
        from backend.core.security import is_safe_file_url
        assert is_safe_file_url("file:///Users/prabaharan/x.html") is False


class TestIdleTTL:
    def test_default_300(self, monkeypatch):
        monkeypatch.delenv("JAMBU_SESSION_IDLE_TTL", raising=False)
        monkeypatch.delenv("JAMBU_BROWSER_IDLE_TTL", raising=False)
        from backend.modules.browser_agent import _idle_ttl_seconds
        assert _idle_ttl_seconds() == 300
    def test_service_prune_by_idle(self):
        from backend.modules.browser_agent_service import BrowserAgentService
        svc = BrowserAgentService(max_sessions=4, ttl_seconds=9999, idle_ttl_seconds=60)
        assert svc.idle_ttl_seconds == 60
    def test_touch_resets_idle(self):
        import time

        from backend.modules.browser_agent import BrowserAgentSession
        class FakePage:
            network_policy = None
            async def snapshot(self):
                return {"elements": [], "text": "", "url": "", "title": ""}
            async def current_url(self):
                return ""
        s = BrowserAgentSession("bs-x", FakePage(), allow_domains=["example.com"])
        s.last_activity = time.time() - 500
        assert s.idle_seconds > 400
        s.touch()
        assert s.idle_seconds < 5


class TestAXTree:
    def _session(self):
        from backend.modules.browser_agent import BrowserAgentSession
        class FakePage:
            network_policy = None
            async def snapshot(self):
                return {"elements": [], "text": "", "url": "", "title": ""}
            async def current_url(self):
                return ""
        s = BrowserAgentSession("bs-ax", FakePage(), allow_domains=["example.com"])
        s.catalog = {
            "e1": {"ref": "e1", "role": "heading", "tag": "h1", "name": "Agathiyar Archive", "href": "", "value": ""},
            "b2": {"ref": "b2", "role": "button", "tag": "button", "name": "Submit Search", "href": "", "value": ""},
        }
        return s
    def test_tree_format(self):
        s = self._session()
        tree = s.ax_tree_text()
        assert tree.startswith("[Root: document]")
        assert "[@1:" in tree and "[@2:" in tree
        assert "Agathiyar Archive" in tree
    def test_act_by_ref_unknown(self):
        from backend.modules.browser_agent_errors import SessionRefused
        s = self._session()
        with pytest.raises(SessionRefused):
            asyncio.run(s.act_by_ax_ref("click", 99, approve=True))


class TestEventBus:
    def test_fanout(self):
        from backend.engine_runtime import manager
        async def go():
            q = manager.session_subscribe("bs-ev")
            await manager.emit_session_event("bs-ev", "nav:start", {"url": "https://example.com"})
            ev = await asyncio.wait_for(q.get(), timeout=2)
            manager.session_unsubscribe("bs-ev", q)
            return ev
        ev = asyncio.run(go())
        assert ev["event"] == "nav:start"
        assert ev["session_id"] == "bs-ev"
        assert ev["data"]["url"] == "https://example.com"


class TestRoutesPresent:
    def test_greenwich_routes_registered(self):
        import backend.routes.browser_sessions as m
        paths = [r.path for r in m.router.routes]
        for suffix in ("/ax-tree", "/act-by-ref", "/input", "/frame", "/stream", "/events", "/sync-greenwich"):
            assert any(p.endswith(suffix) for p in paths), f"missing {suffix}"
    def test_research_sync_flags(self):
        from backend.routes.research import ResearchRequest, ScrapeRequest
        assert ResearchRequest(query="q").sync_to_greenwich_memory is False
        assert ScrapeRequest(url="https://example.com").sync_to_greenwich_memory is False
    def test_audit_sync_flags(self):
        from backend.routes.audit import AuditRunRequest
        r = AuditRunRequest(url="https://example.com")
        assert r.sync_to_greenwich_memory is False
        assert r.session_id is None
    def test_headless_profile(self):
        from backend.modules.browser import _chromium_headless_args
        args = _chromium_headless_args()
        assert "--disable-gpu-compositing" in args
        assert "--headless=new" in args


class TestFileUrlRoutePolicy:
    """Regression: the request policy must admit file:// or the route aborts it."""

    def test_network_policy_allows_workspace_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOW_LOCAL_WORKSPACES", "true")
        monkeypatch.setenv("JAMBU_LOCAL_WORKSPACE_ROOTS", str(tmp_path))
        from backend.modules.browser_debug import NetworkPolicy
        policy = NetworkPolicy(["127.0.0.1"])
        inside = tmp_path / "x.html"
        inside.write_text("<h1>hi</h1>")
        decision = policy.decide(inside.as_uri(), kind="document")
        assert decision.allowed and decision.reason == "local_workspace"

    def test_network_policy_blocks_file_outside_roots(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOW_LOCAL_WORKSPACES", "true")
        monkeypatch.setenv("JAMBU_LOCAL_WORKSPACE_ROOTS", str(tmp_path))
        from backend.modules.browser_debug import NetworkPolicy
        decision = NetworkPolicy(["127.0.0.1"]).decide("file:///etc/passwd", kind="document")
        assert not decision.allowed

    def test_post_nav_gate_accepts_file_url(self, monkeypatch, tmp_path):
        """Regression: file:// pages have no host, so host_allowed reverted them."""
        monkeypatch.setenv("ALLOW_LOCAL_WORKSPACES", "true")
        monkeypatch.setenv("JAMBU_LOCAL_WORKSPACE_ROOTS", str(tmp_path))
        from backend.modules.browser_agent import BrowserAgentSession

        class FakePage:
            network_policy = None
            async def snapshot(self):
                return {"elements": [], "text": "", "url": "", "title": ""}
            async def current_url(self):
                return ""

        session = BrowserAgentSession("bs-file", FakePage(), allow_domains=["127.0.0.1"])
        inside = tmp_path / "doc.html"
        inside.write_text("hi")
        assert session._url_allowed_after_nav(inside.as_uri()) is True
        assert session._url_allowed_after_nav("file:///etc/passwd") is False
        assert session._url_allowed_after_nav("https://not-allowed.example/") is False


class TestGreenwichBridgeModule:
    def test_spec_payload_shape(self):
        from backend.modules.greenwich_bridge import _spec_payload
        body = _spec_payload(title="t", clean_markdown="md", url="https://x.test",
                             citations=["https://x.test"], metadata={"session_id": "bs-1"})
        assert body["source"] == "jambubrowser"
        assert body["clean_markdown"] == "md"
        assert body["metadata"]["session_id"] == "bs-1"

    def test_trajectory_payload_shape(self):
        """Deployed memory_bridge.py ingests TITO records, not spec payloads."""
        from backend.modules.greenwich_bridge import _trajectory_payload
        body = _trajectory_payload(title="Palm Leaf Vol. 4", clean_markdown="verse 0001",
                                   url="https://x.test", citations=[], metadata={"audit_score": 98.4})
        assert body["input_text"] == "Palm Leaf Vol. 4"
        assert body["output_text"] == "verse 0001"
        assert body["role"] == "main_agent_task"
        assert body["metadata"]["source"] == "jambubrowser"
        assert body["metadata"]["audit_score"] == 98.4

    def test_ingest_endpoint_order(self):
        from backend.modules.greenwich_bridge import INGEST_PATHS
        assert INGEST_PATHS[0] == "/v1/memory/ingest"
        assert INGEST_PATHS[1] == "/v1/trajectory/record"

    def test_memory_bridge_url_env(self, monkeypatch):
        monkeypatch.setenv("GREENWICH_MEMORY_URL", "http://127.0.0.1:9999/")
        from backend.modules.greenwich_bridge import memory_bridge_url
        assert memory_bridge_url() == "http://127.0.0.1:9999"
