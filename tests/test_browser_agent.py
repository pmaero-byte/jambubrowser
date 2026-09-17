"""
Browser-agent session tests — allowlists, approval gates, PII scrubbing,
receipts, evidence, and the routes.

A scripted fake page exercises the safety semantics without a browser;
the real Playwright path is exercised by live verification and the audit
pipeline tests.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.modules.browser_agent import (
    BrowserAgentSession,
    BrowserAgentService,
    SessionRefused,
    classify_risk,
    host_allowed,
)
from backend.modules.evidence import verify_bundle


def run(coro):
    return asyncio.run(coro)


class FakePage:
    """Scripted page: DOM catalog + navigations, including traps."""

    def __init__(self):
        self.url = "about:blank"
        self.elements: list[dict] = []
        self.text = ""
        self.title = "Fake"
        self.clicks: list[str] = []
        self.typed: list[tuple[str, str]] = []
        self.gotos: list[str] = []
        self.redirect_to: str | None = None  # simulate a redirect on goto

    async def goto(self, url: str) -> None:
        self.gotos.append(url)
        self.url = self.redirect_to or url
        self.redirect_to = None

    async def snapshot(self) -> dict:
        return {
            "url": self.url, "title": self.title,
            "elements": self.elements, "text": self.text,
        }

    async def click(self, ref: str) -> None:
        self.clicks.append(ref)
        target = next((e for e in self.elements if e["ref"] == ref), None)
        if target and target.get("href", "").startswith("http"):
            self.url = target["href"]

    async def type_text(self, ref: str, text: str) -> None:
        self.typed.append((ref, text))

    async def current_url(self) -> str:
        return self.url


def make_session(**kwargs) -> BrowserAgentSession:
    defaults = dict(allow_domains=["example.com"], require_approval=True)
    defaults.update(kwargs)
    return BrowserAgentSession("bs-test", FakePage(), **defaults)


def seed_elements(page: FakePage) -> None:
    page.elements = [
        {"ref": "@e1", "tag": "a", "role": "", "type": "", "name": "Home",
         "href": "https://example.com/home"},
        {"ref": "@e2", "tag": "a", "role": "", "type": "", "name": "Escape",
         "href": "https://evil.example.net/steal"},
        {"ref": "@e3", "tag": "button", "role": "", "type": "",
         "name": "Delete account", "href": ""},
        {"ref": "@e4", "tag": "input", "role": "", "type": "email",
         "name": "Email", "href": ""},
    ]
    page.text = "Contact: alice@example.com or 555-123-4567"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_host_allowed_exact_and_subdomain_only(self):
        assert host_allowed("example.com", ["example.com"]) is True
        assert host_allowed("www.example.com", ["example.com"]) is True
        assert host_allowed("example.com.evil.net", ["example.com"]) is False
        assert host_allowed("evil-example.com", ["example.com"]) is False
        assert host_allowed("", ["example.com"]) is False

    def test_wildcard_allowlist_is_deny_all_not_allow_all(self):
        """A caller asking for '*' gets nothing (fail closed), not everything."""
        assert host_allowed("example.com", ["*"]) is False
        session = make_session(allow_domains=["*"])
        with pytest.raises(SessionRefused) as e:
            run(session.navigate("https://example.com/"))
        assert e.value.reason == "blocked_domain"

    def test_risk_classifier(self):
        assert classify_risk("Delete account") == "delete"
        assert classify_risk("Buy now", "") == "buy"
        assert classify_risk("Sign in") is None
        assert classify_risk("", "https://shop.example.com/checkout") == "checkout"


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_open_requires_allowlist(self):
        with pytest.raises(ValueError):
            BrowserAgentSession("bs", FakePage(), allow_domains=[])

    def test_navigation_inside_allowlist(self):
        session = make_session()
        result = run(session.navigate("https://www.example.com/page"))
        assert result["url"] == "https://www.example.com/page"

    def test_navigation_outside_allowlist_is_blocked_and_recorded(self):
        session = make_session()
        with pytest.raises(SessionRefused) as e:
            run(session.navigate("https://other.org/"))
        assert e.value.reason == "blocked_domain"
        receipts = session.receipts()
        assert receipts["steps"][-1]["outcome"] == "blocked"
        assert receipts["steps"][-1]["action"] == "navigate"

    def test_unsafe_url_is_blocked(self):
        session = make_session(allow_domains=["127.0.0.1"])
        with pytest.raises(SessionRefused) as e:
            run(session.navigate("http://127.0.0.1:9999/admin"))
        assert e.value.reason == "unsafe_url"

    def test_redirect_outside_allowlist_is_reverted(self):
        session = make_session()
        session.page.redirect_to = "https://evil.example.net/landing"
        with pytest.raises(SessionRefused) as e:
            run(session.navigate("https://example.com/ok"))
        assert e.value.reason == "blocked_domain"
        assert session.page.url == "about:blank"          # reverted
        assert session.receipts()["steps"][-1]["outcome"] == "reverted"

    def test_click_on_external_link_is_refused_before_clicking(self):
        session = make_session()
        page = session.page
        seed_elements(page)
        run(session.snapshot())
        with pytest.raises(SessionRefused) as e:
            run(session.act("click", "@e2", approve=True))
        assert e.value.reason == "blocked_domain"
        assert page.clicks == []                          # never clicked

    def test_click_that_navigates_outside_is_reverted(self):
        session = make_session()
        page = session.page
        seed_elements(page)
        # Point the same ref at an in-allowlist href, but make the fake page
        # navigate somewhere else (simulating a JS-driven redirect).
        page.elements[0]["href"] = "https://example.com/fine"
        run(session.snapshot())
        original_click = page.click

        async def sneaky_click(ref):
            await original_click(ref)
            page.url = "https://evil.example.net/trap"

        page.click = sneaky_click
        with pytest.raises(SessionRefused) as e:
            run(session.act("click", "@e1", approve=True))
        assert e.value.reason == "blocked_domain"
        assert page.url == "about:blank"
        assert session.receipts()["steps"][-1]["outcome"] == "reverted"


# ---------------------------------------------------------------------------
# Approval gates
# ---------------------------------------------------------------------------

class TestApprovals:
    def test_session_gate_refuses_without_approval(self):
        session = make_session()
        seed_elements(session.page)
        run(session.snapshot())
        with pytest.raises(SessionRefused) as e:
            run(session.act("type", "@e4", text="hi@example.com"))
        assert e.value.reason == "approval_required"
        assert session.page.typed == []

    def test_risky_element_always_requires_approval_even_when_session_does_not(self):
        session = make_session(require_approval=False)
        seed_elements(session.page)
        run(session.snapshot())
        with pytest.raises(SessionRefused) as e:
            run(session.act("click", "@e3"))   # "Delete account"
        assert e.value.reason == "approval_required"
        assert session.page.clicks == []

        result = run(session.act("click", "@e3", approve=True))
        assert result["outcome"] == "ok"
        assert session.page.clicks == ["@e3"]

    def test_approval_lets_normal_actions_through(self):
        session = make_session()
        seed_elements(session.page)
        run(session.snapshot())
        result = run(session.act("type", "@e4", text="hi@example.com", approve=True))
        assert result["outcome"] == "ok"
        assert session.page.typed == [("@e4", "hi@example.com")]

    def test_unknown_ref_is_refused(self):
        session = make_session()
        run(session.snapshot())
        with pytest.raises(SessionRefused) as e:
            run(session.act("click", "@e99", approve=True))
        assert e.value.reason == "unknown_ref"


# ---------------------------------------------------------------------------
# PII scrubbing
# ---------------------------------------------------------------------------

class TestScrubbing:
    def test_snapshot_masks_pii(self):
        session = make_session()
        seed_elements(session.page)
        result = run(session.snapshot())
        assert "alice@example.com" not in result["text"]
        assert "555-123-4567" not in result["text"]
        assert "***" in result["text"] or "REDACTED" in result["text"]

    def test_scrubbing_can_be_disabled(self):
        session = make_session(scrub_pii=False)
        seed_elements(session.page)
        result = run(session.snapshot())
        assert "alice@example.com" in result["text"]


# ---------------------------------------------------------------------------
# Receipts + evidence
# ---------------------------------------------------------------------------

class TestReceipts:
    def test_receipts_chain_and_merkle_root(self):
        session = make_session()
        seed_elements(session.page)
        run(session.navigate("https://example.com/"))
        run(session.snapshot())
        run(session.act("type", "@e4", text="x", approve=True))

        receipts = session.receipts()
        assert receipts["count"] == 3
        hashes = [s["step_hash"] for s in receipts["steps"]]
        assert all(hashes)
        assert receipts["chain_head"] == hashes[-1]

        from backend.modules.meshpay import js_dumps, merkle_root
        from backend.modules.browser_agent import Step
        import hashlib

        # Recompute the chain exactly as the service does.
        prev = None
        for step in receipts["steps"]:
            payload = {k: step[k] for k in
                       ("seq", "ts", "action", "outcome", "url", "ref", "detail", "prev_hash")}
            assert payload["prev_hash"] == prev
            expected = hashlib.sha256(js_dumps(payload).encode()).hexdigest()
            assert step["step_hash"] == expected
            prev = step["step_hash"]
        assert receipts["merkle_root"] == merkle_root(hashes)

    def test_session_evidence_bundle_verifies(self, monkeypatch):
        monkeypatch.setenv("JAMBU_EVIDENCE_KEY", "22" * 32)
        session = make_session()
        seed_elements(session.page)
        run(session.navigate("https://example.com/"))
        run(session.snapshot())

        from backend.modules.evidence import build_bundle
        bundle = build_bundle(
            "browser_session",
            {"session_id": session.id, "allow_domains": session.allow_domains},
            session.receipts(),
        )
        assert verify_bundle(bundle)["valid"] is True

        tampered = dict(bundle)
        tampered["payload"] = dict(bundle["payload"])
        tampered["payload"]["steps"] = tampered["payload"]["steps"][:-1]
        assert verify_bundle(tampered)["valid"] is False


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------

class TestService:
    def test_session_caps_and_close(self):
        service = BrowserAgentService(max_sessions=1)
        # Inject a session directly (no browser launch).
        service._sessions["bs-1"] = make_session()
        with pytest.raises(SessionRefused) as e:
            run(service.open(allow_domains=["example.com"]))
        assert e.value.reason == "session_limit"

        info = run(service.close("bs-1"))
        assert info["closed"] is True
        with pytest.raises(SessionRefused):
            service.get("bs-1")

    def test_ttl_prunes_expired_sessions(self):
        import time as _time

        service = BrowserAgentService(ttl_seconds=1)
        session = make_session()
        session.created_at = _time.time() - 5
        service._sessions[session.id] = session
        assert service.list() == []
        with pytest.raises(SessionRefused):
            service.get(session.id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestRoutes:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.engine import app
        from backend.modules import browser_agent

        browser_agent.reset_browser_agent_service()
        with TestClient(app) as c:
            yield c
        browser_agent.reset_browser_agent_service()

    def _install_session(self, monkeypatch, **kwargs):
        from backend.modules import browser_agent

        service = browser_agent.get_browser_agent_service()
        session = make_session(**kwargs)
        service._sessions[session.id] = session
        return session

    def test_open_requires_allowlist(self, client):
        resp = client.post("/browser/sessions", json={"allow_domains": []})
        assert resp.status_code == 422

    def test_full_flow(self, client, monkeypatch):
        session = self._install_session(monkeypatch)
        seed_elements(session.page)
        sid = session.id

        info = client.get("/browser/sessions").json()
        assert info["count"] == 1

        nav = client.post(f"/browser/sessions/{sid}/navigate",
                          json={"url": "https://example.com/"})
        assert nav.status_code == 200

        snap = client.get(f"/browser/sessions/{sid}/snapshot")
        assert snap.status_code == 200
        body = snap.json()
        assert body["count"] == 4
        assert any(e["risk"] == "delete" for e in body["elements"])

        blocked = client.post(f"/browser/sessions/{sid}/navigate",
                              json={"url": "https://evil.example.net/"})
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["reason"] == "blocked_domain"

        gated = client.post(f"/browser/sessions/{sid}/act",
                            json={"action": "click", "ref": "@e3"})
        assert gated.status_code == 403
        assert gated.json()["detail"]["reason"] == "approval_required"

        ok = client.post(f"/browser/sessions/{sid}/act",
                         json={"action": "click", "ref": "@e3", "approve": True})
        assert ok.status_code == 200

        receipts = client.get(f"/browser/sessions/{sid}/receipts").json()
        # navigate ok, snapshot, blocked navigate, gated click, approved click
        assert receipts["count"] == 5
        assert receipts["merkle_root"]

        evidence = client.post(f"/browser/sessions/{sid}/evidence")
        assert evidence.status_code == 200, evidence.text
        bundle = evidence.json()
        assert bundle["kind"] == "browser_session"
        assert verify_bundle(bundle)["valid"] is True

        closed = client.delete(f"/browser/sessions/{sid}")
        assert closed.status_code == 200

    def test_unknown_session_is_404(self, client):
        assert client.get("/browser/sessions/nope").status_code == 404
        assert client.get("/browser/sessions/nope/snapshot").status_code == 404
