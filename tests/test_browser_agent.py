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
from backend.decentralized.evidence import verify_bundle


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

        from backend.decentralized.meshpay import js_dumps, merkle_root
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

        from backend.decentralized.evidence import build_bundle
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

class TestCompactSnapshot:
    """The observation projector, wired to the live session snapshot path.

    ``compact_observation`` used to be imported by the agent and never
    called, so nothing exercised it end to end.
    """

    def test_default_snapshot_is_unchanged(self):
        session = make_session()
        seed_elements(session.page)
        result = run(session.snapshot())
        assert result["count"] == 4
        assert len(result["elements"]) == 4      # full catalog, not a projection

    def test_compact_returns_rows_not_elements(self):
        session = make_session()
        seed_elements(session.page)
        view = run(session.snapshot(compact=True))
        assert "elements" not in view
        assert view["columns"] == ["ref", "role", "name"]
        assert view["shown"] == 4
        assert view["total"] == 4
        assert view["tokens_estimate"] > 0

    def test_compact_still_refreshes_the_catalog(self):
        """A compact read must leave ``act`` able to resolve the same refs."""
        session = make_session()
        seed_elements(session.page)
        view = run(session.snapshot(compact=True))
        ref = view["rows"][0][0]
        assert ref in session.catalog
        result = run(session.act("click", ref, approve=True))
        assert result["outcome"] == "ok"

    def test_first_delta_lists_rows_rather_than_reporting_all_added(self):
        """With no previous catalog a delta would be a lie ("everything added")."""
        session = make_session()
        seed_elements(session.page)
        view = run(session.snapshot(compact=True, delta=True))
        assert "rows" in view
        assert "delta" not in view
        assert view["shown"] == 4

    def test_delta_reports_only_what_moved(self):
        session = make_session()
        seed_elements(session.page)
        run(session.snapshot(compact=True))          # establishes a baseline
        page = session.page
        page.elements[3]["value"] = "typed@example.com"   # the Email input
        page.elements.append({
            "ref": "@e5", "tag": "a", "role": "", "type": "",
            "name": "Sign out", "href": "https://example.com/out",
        })
        view = run(session.snapshot(compact=True, delta=True))
        assert "rows" not in view
        assert view["delta"]["counts"]["added"] == 1
        assert view["delta"]["counts"]["changed"] == 1
        assert view["shown"] == 2
        assert any("Sign out" in cell for cell in view["delta"]["added"][0])

    def test_query_and_roles_narrow_through_the_session(self):
        session = make_session()
        seed_elements(session.page)
        view = run(session.snapshot(compact=True, observe={"query": "email"}))
        assert view["matched"] == 1
        assert "Email" in view["rows"][0]

    def test_unknown_observe_option_is_refused_with_the_valid_set(self):
        session = make_session()
        seed_elements(session.page)
        with pytest.raises(SessionRefused) as e:
            run(session.snapshot(compact=True, observe={"nonsense": 1}))
        assert e.value.reason == "invalid_observation"
        assert "nonsense" in e.value.detail

    def test_pii_is_still_scrubbed_in_a_compact_observation(self):
        """The projection must not become a scrubbing bypass."""
        session = make_session()
        seed_elements(session.page)
        view = run(session.snapshot(compact=True, observe={"text": "contact"}))
        blob = str(view)
        assert "alice@example.com" not in blob
        assert "555-123-4567" not in blob


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

    def test_snapshot_compact_query_params(self, client, monkeypatch):
        session = self._install_session(monkeypatch)
        seed_elements(session.page)
        sid = session.id

        full = client.get(f"/browser/sessions/{sid}/snapshot").json()
        assert "elements" in full                     # default unchanged

        compact = client.get(
            f"/browser/sessions/{sid}/snapshot",
            params={"compact": "true", "query": "email"},
        )
        assert compact.status_code == 200
        body = compact.json()
        assert "elements" not in body
        assert body["matched"] == 1
        assert body["columns"] == ["ref", "role", "name"]

        by_role = client.get(
            f"/browser/sessions/{sid}/snapshot",
            params={"compact": "true", "roles": "button", "fields": "ref,name"},
        ).json()
        assert by_role["matched"] == 1                 # only "Delete account"
        assert by_role["columns"] == ["ref", "name"]

        # 'all' needs every word in one element; 'any' is a candidate set.
        strict = client.get(
            f"/browser/sessions/{sid}/snapshot",
            params={"compact": "true", "query": "email delete"},
        ).json()
        assert strict["matched"] == 0
        loose = client.get(
            f"/browser/sessions/{sid}/snapshot",
            params={"compact": "true", "query": "email delete", "match": "any"},
        ).json()
        assert loose["matched"] == 2

    def test_snapshot_delta_over_http(self, client, monkeypatch):
        session = self._install_session(monkeypatch)
        seed_elements(session.page)
        sid = session.id
        client.get(f"/browser/sessions/{sid}/snapshot")   # baseline

        session.page.elements.append({
            "ref": "@e5", "tag": "a", "role": "", "type": "",
            "name": "Sign out", "href": "https://example.com/out",
        })
        body = client.get(
            f"/browser/sessions/{sid}/snapshot", params={"delta": "true"},
        ).json()
        assert body["delta"]["counts"]["added"] == 1
        assert body["shown"] == 1


# ---------------------------------------------------------------------------
# Regression: latent NameErrors in rarely-taken branches
# ---------------------------------------------------------------------------

class FakeResponse:
    """Playwright-APIResponse stand-in (only the attributes http_request reads)."""

    def __init__(self, status, body="{}", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.ok = 200 <= status < 300

    def json(self):
        import json as _json

        return _json.loads(self._body)

    @property
    def text(self):
        return self._body


class FakeRequestContext:
    """Minimal APIRequestContext stand-in that answers with a redirect chain."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []

    async def fetch(self, url, **kwargs):
        self.calls.append(url)
        return self.responses.pop(0)


class TestLatentNameErrors:
    """Branches no test used to reach, where the name was never defined.

    Each of these raised NameError instead of doing its job, which is the
    worst kind of bug: it only appears when the surrounding feature is used
    with the "unusual" configuration (no network policy, fresh manager,
    history that does not end in a user message).
    """

    def test_http_request_follows_redirect_without_a_network_policy(self):
        # A raw adapter has network_policy=None. The redirect fallback built a
        # NetworkDecision for "no policy configured" — with the name never
        # imported, every 3xx became a NameError.
        from backend.modules.browser_agent import PlaywrightPage

        ctx = FakeRequestContext([
            FakeResponse(302, headers={"location": "https://example.com/final"}),
            FakeResponse(200, body='{"ok": true}'),
        ])
        raw = FakePage()
        raw.context = type("Ctx", (), {"request": ctx})()
        adapter = PlaywrightPage(raw)
        assert adapter.network_policy is None

        result = run(adapter.http_request("GET", "https://example.com/start"))

        assert result["status"] == 200
        assert result["ok"] is True
        assert ctx.calls == [
            "https://example.com/start",
            "https://example.com/final",
        ]

    def test_federated_rag_constructs(self):
        # Fernet was referenced but never imported, so *every* construction
        # raised NameError — the module was unusable, not just untested.
        from backend.decentralized.federated_rag import FederatedRAG

        assert FederatedRAG()._cipher is not None
        # An explicit key must round-trip through the cipher.
        from cryptography.fernet import Fernet

        keyed = FederatedRAG(encryption_key=Fernet.generate_key())
        assert keyed._cipher is not None
