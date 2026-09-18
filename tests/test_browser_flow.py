"""
Tests for the token-efficient browser test flow: local-dev opt-in,
intent-based target resolution, declarative steps, assertions, auto-telemetry,
and the one-shot run/test routes.

A scripted ``FlowPage`` exercises the semantics without launching a browser.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.modules.browser_agent import (
    BrowserAgentSession,
    BrowserAgentService,
    SessionRefused,
    normalize_flow_steps,
    render_flow_report,
)


def run(coro):
    return asyncio.run(coro)


class FlowPage:
    """A richer scripted page: catalog, navigation, telemetry, interactions."""

    def __init__(self):
        self.url = "about:blank"
        self.title = "Test App"
        self.text = "Welcome to the test app"
        self.elements: list[dict] = []
        self.clicks: list[str] = []
        self.typed: list[tuple[str, str]] = []
        self.pressed: list[tuple[str, str]] = []
        self.hovered: list[str] = []
        self.selected: list[tuple[str, str]] = []
        self.checked: list[tuple[str, bool]] = []
        self.reloaded = 0
        self.gotos: list[str] = []
        self.console = []
        self.page_errors = []
        self.failed_requests = []

    async def goto(self, url: str) -> None:
        self.gotos.append(url)
        self.url = url

    async def snapshot(self) -> dict:
        return {
            "url": self.url, "title": self.title,
            "elements": self.elements, "text": self.text,
        }

    async def click(self, ref: str) -> None:
        self.clicks.append(ref)
        target = next((e for e in self.elements if e["ref"] == ref), None)
        if not target:
            return
        if target.get("type") == "checkbox" or target.get("role") == "checkbox":
            target["checked"] = not target.get("checked")
        if target.get("navigates_to"):
            self.url = target["navigates_to"]
            self.title = target.get("new_title", self.title)
            self.elements = target.get("new_elements", [])
        if target.get("reveals"):
            self.elements.extend(target["reveals"])

    async def type_text(self, ref: str, text: str) -> None:
        self.typed.append((ref, text))
        for e in self.elements:
            if e["ref"] == ref:
                e["value"] = text

    async def current_url(self) -> str:
        return self.url

    async def wait_for(self, *, timeout_ms: int = 5000) -> None:
        return None

    async def wait_for_selector(self, selector: str, timeout_ms: int = 5000) -> None:
        return None

    async def wait_for_text(self, text: str, timeout_ms: int = 5000) -> None:
        if text and text not in self.text:
            raise SessionRefused("wait_timeout", f"text {text!r} never appeared")

    async def press(self, ref: str, key: str) -> None:
        self.pressed.append((ref, key))

    async def hover(self, ref: str) -> None:
        self.hovered.append(ref)

    async def select_option(self, ref: str, value: str) -> None:
        self.selected.append((ref, value))

    async def check(self, ref: str, checked: bool = True) -> None:
        self.checked.append((ref, checked))
        for e in self.elements:
            if e["ref"] == ref:
                e["checked"] = checked

    async def reload(self) -> None:
        self.reloaded += 1

    async def go_back(self) -> None:
        return None

    async def go_forward(self) -> None:
        return None

    async def screenshot(self, full_page: bool = False) -> str:
        return "QUJD"

    def peek_telemetry(self) -> dict:
        return {
            "console_errors": list(self.console),
            "console_warnings": [],
            "failed_requests": list(self.failed_requests),
            "bad_responses": [],
        }

    def drain_telemetry(self) -> dict:
        data = self.peek_telemetry()
        self.console.clear()
        self.failed_requests.clear()
        return data


def seed(page: FlowPage) -> None:
    page.elements = [
        {"ref": "@e1", "tag": "a", "role": "", "type": "", "name": "Home",
         "href": "https://example.com/home", "visible": True,
         "navigates_to": "https://example.com/home", "new_title": "Home Page",
         "new_elements": [
             {"ref": "@n1", "tag": "h1", "role": "", "type": "",
              "name": "Dashboard", "href": "", "visible": True},
         ]},
        {"ref": "@e2", "tag": "input", "role": "", "type": "email",
         "name": "Email", "href": "", "visible": True, "value": ""},
        {"ref": "@e3", "tag": "button", "role": "", "type": "",
         "name": "Sign in", "href": "", "visible": True},
        {"ref": "@e4", "tag": "button", "role": "", "type": "",
         "name": "Delete account", "href": "", "visible": True},
        {"ref": "@e5", "tag": "input", "role": "", "type": "checkbox",
         "name": "Remember me", "href": "", "visible": True, "checked": False},
        {"ref": "@e6", "tag": "div", "role": "", "type": "",
         "name": "Hidden note", "href": "", "visible": False},
    ]


def make_session(page: FlowPage | None = None, **kwargs) -> BrowserAgentSession:
    page = page or FlowPage()
    defaults = dict(allow_domains=["example.com"], require_approval=False)
    defaults.update(kwargs)
    return BrowserAgentSession("bs-test", page, **defaults)


# ---------------------------------------------------------------------------
# normalize_flow_steps
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_bare_strings_become_navigations(self):
        steps = normalize_flow_steps(["https://a.example.com", "https://b.example.com"])
        assert steps == [
            {"action": "navigate", "url": "https://a.example.com"},
            {"action": "navigate", "url": "https://b.example.com"},
        ]

    def test_json_string_and_wrapper(self):
        steps = normalize_flow_steps('{"steps": [{"action": "click", "target": "Go"}]}')
        assert steps[0]["action"] == "click"

    def test_empty_is_refused(self):
        with pytest.raises(SessionRefused):
            normalize_flow_steps([])

    def test_invalid_json_is_refused(self):
        with pytest.raises(SessionRefused):
            normalize_flow_steps("{not json")

    def test_too_long_is_refused(self):
        with pytest.raises(SessionRefused):
            normalize_flow_steps([{"action": "wait"}] * 101)


# ---------------------------------------------------------------------------
# Intent-based target resolution
# ---------------------------------------------------------------------------

class TestResolveTarget:
    def test_exact_name_and_ref(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        run(session._read_state())
        assert session.resolve_target("@e3") == "@e3"
        assert session.resolve_target("Sign in") == "@e3"
        assert session.resolve_target("sign in") == "@e3"

    def test_role_prefixed(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        run(session._read_state())
        assert session.resolve_target("input Email") == "@e2"

    def test_unique_substring(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        run(session._read_state())
        assert session.resolve_target("Delete") == "@e4"

    def test_ambiguous_reports_candidates(self):
        page = FlowPage()
        page.elements = [
            {"ref": "@e1", "name": "Save draft"},
            {"ref": "@e2", "name": "Save and exit"},
        ]
        session = make_session(page)
        run(session._read_state())
        with pytest.raises(SessionRefused) as exc:
            session.resolve_target("Save")
        assert exc.value.reason == "target_ambiguous"
        assert len(exc.value.candidates) == 2

    def test_missing_reports_nothing(self):
        session = make_session()
        run(session._read_state())
        with pytest.raises(SessionRefused) as exc:
            session.resolve_target("Nonexistent")
        assert exc.value.reason == "target_not_found"


# ---------------------------------------------------------------------------
# Flow runner
# ---------------------------------------------------------------------------

class TestFlowRunner:
    def test_full_flow_by_intent_passes(self):
        page = FlowPage()
        seed(page)
        session = make_session(page, allow_domains=["example.com"])
        report = run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
            {"action": "assert_visible", "target": "Sign in"},
            {"action": "type", "target": "Email", "value": "user@example.com"},
            {"action": "click", "target": "Remember me"},
            {"action": "assert_checked", "target": "Remember me"},
        ]))
        assert report["ok"] is True
        assert report["passed"] == 5 and report["failed"] == 0
        assert page.typed == [("@e2", "user@example.com")]
        assert next(e for e in page.elements if e["ref"] == "@e5")["checked"] is True

    def test_clicks_auto_observe_new_page(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
            {"action": "click", "target": "Home"},
            {"action": "assert_visible", "target": "Dashboard"},
        ]))
        assert report["ok"] is True
        assert report["final_url"] == "https://example.com/home"

    def test_failed_assertion_marks_step_failed(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
            {"action": "assert_visible", "target": "Ghost"},
        ]))
        assert report["ok"] is False
        assert report["failed"] == 1
        assert report["steps"][1]["reason"] == "assertion_failed"

    def test_visible_falls_back_to_page_text_for_non_interactive(self):
        page = FlowPage()
        seed(page)
        page.text = "Welcome to the test app. Revealed secret panel"
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "assert_visible", "target": "Revealed secret panel"},
        ]))
        assert report["ok"] is True
        assert "visible text" in report["steps"][0]["detail"]

    def test_hidden_text_assertion_fails_when_not_rendered(self):
        page = FlowPage()
        seed(page)
        page.text = "Welcome to the test app"
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "assert_visible", "target": "Revealed secret panel"},
        ]))
        assert report["ok"] is False

    def test_stop_on_failure(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "assert_visible", "target": "Ghost"},
            {"action": "assert_visible", "target": "Sign in"},
        ], stop_on_failure=True))
        assert report["total"] == 1

    def test_telemetry_is_attached_to_report(self):
        page = FlowPage()
        seed(page)
        page.console = ["TypeError: x is undefined"]
        page.failed_requests = [{"method": "GET", "url": "https://x/y", "failure": "net::ERR"}]
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
            {"action": "assert_console_clean"},
        ]))
        assert report["ok"] is False
        assert "TypeError" in report["console_errors"][0]
        assert report["failed_requests"][0]["failure"] == "net::ERR"

    def test_screenshot_step_returns_base64(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([{"action": "screenshot"}]))
        assert report["steps"][0]["screenshot_base64"] == "QUJD"

    def test_reload_press_and_wait(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "reload"},
            {"action": "press", "key": "Enter"},
            {"action": "wait", "text": "Welcome"},
        ]))
        assert report["ok"] is True
        assert page.reloaded == 1
        assert page.pressed == [("", "Enter")]

    def test_unsupported_action_fails_cleanly(self):
        page = FlowPage()
        session = make_session(page)
        report = run(session.run_flow([{"action": "warp"}]))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "unknown_action"

    def test_flow_records_receipt(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        run(session.run_flow([{"action": "navigate", "url": "https://example.com/"}]))
        receipts = session.receipts()
        assert receipts["steps"][-1]["action"] == "run_flow"


# ---------------------------------------------------------------------------
# Local-dev opt-in
# ---------------------------------------------------------------------------

class TestLocalMode:
    def test_localhost_blocked_by_default(self):
        session = make_session(allow_domains=["localhost"])
        with pytest.raises(SessionRefused) as exc:
            run(session.navigate("http://localhost:3000/"))
        assert exc.value.reason == "unsafe_url"

    def test_localhost_allowed_when_allow_private(self):
        page = FlowPage()
        session = make_session(page, allow_domains=["localhost"], allow_private=True)
        result = run(session.navigate("http://localhost:3000/"))
        assert result["url"] == "http://localhost:3000/"

    def test_private_ip_allowed_only_when_opted_in(self):
        session = make_session(allow_domains=["192.168.1.10"], allow_private=True)
        result = run(session.navigate("http://192.168.1.10:8000/"))
        assert result["url"] == "http://192.168.1.10:8000/"

    def test_local_flag_does_not_bypass_allowlist(self):
        page = FlowPage()
        session = make_session(page, allow_domains=["localhost"], allow_private=True)
        with pytest.raises(SessionRefused) as exc:
            run(session.navigate("http://127.0.0.1:3000/"))
        assert exc.value.reason == "blocked_domain"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class TestRender:
    def test_report_is_compact(self):
        report = {
            "ok": False, "passed": 1, "failed": 1, "total": 2, "duration_ms": 42,
            "final_url": "http://localhost:3000", "title": "App",
            "steps": [
                {"i": 1, "action": "navigate", "status": "passed", "detail": "→ x"},
                {"i": 2, "action": "click", "status": "failed",
                 "reason": "target_not_found", "error": "no element matches 'Buy'"},
            ],
            "console_errors": ["boom"],
        }
        text = render_flow_report(report)
        assert "Browser test FAIL" in text
        assert "1/2 steps" in text
        assert "target_not_found" in text
        assert "boom" in text


# ---------------------------------------------------------------------------
# One-shot service path (open → run → close)
# ---------------------------------------------------------------------------

class TestRunTest:
    def test_run_test_opens_runs_and_closes(self, monkeypatch):
        page = FlowPage()
        seed(page)
        service = BrowserAgentService()
        session = make_session(page, allow_domains=["localhost"], allow_private=True)
        opened, closed = {}, {}

        async def fake_open(**kwargs):
            opened.update(kwargs)
            service._sessions[session.id] = session
            return session

        async def fake_close(session_id):
            closed["id"] = session_id
            service._sessions.pop(session_id, None)
            return {"session_id": session_id, "closed": True}

        monkeypatch.setattr(service, "open", fake_open)
        monkeypatch.setattr(service, "close", fake_close)

        report = run(service.run_test(
            url="http://localhost:3000/",
            steps=[{"action": "assert_visible", "target": "Sign in"}],
            local=True,
        ))
        assert report["ok"] is True
        assert opened["allow_private"] is True
        assert "localhost" in opened["allow_domains"]
        assert closed["id"] == session.id
        assert report["receipts"]["count"] >= 2

    def test_close_tears_down_browser_via_stop(self):
        service = BrowserAgentService()
        session = make_session()
        stopped = {}

        class FakeBrowser:
            async def stop(self):
                stopped["stop"] = True

        service._sessions[session.id] = session
        service._browser_sessions[session.id] = FakeBrowser()
        result = run(service.close(session.id))
        assert result["closed"] is True
        assert stopped["stop"] is True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestRoutes:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from backend.engine import app
        from backend.modules import browser_agent

        browser_agent.reset_browser_agent_service()
        with TestClient(app) as c:
            yield c
        browser_agent.reset_browser_agent_service()

    def _install(self):
        from backend.modules import browser_agent

        page = FlowPage()
        seed(page)
        session = make_session(page)
        browser_agent.get_browser_agent_service()._sessions[session.id] = session
        return session

    def test_run_flow_on_session(self, client):
        session = self._install()
        resp = client.post(f"/browser/sessions/{session.id}/run", json={
            "steps": [
                {"action": "navigate", "url": "https://example.com/"},
                {"action": "click", "target": "Home"},
                {"action": "assert_visible", "target": "Dashboard"},
            ],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["passed"] == 3

    def test_open_accepts_allow_private(self, client, monkeypatch):
        from backend.routes import browser_sessions as routes
        from backend.modules import browser_agent

        service = browser_agent.get_browser_agent_service()
        captured = {}

        async def fake_open(**kwargs):
            captured.update(kwargs)
            raise browser_agent.SessionRefused("session_limit", "stub")

        monkeypatch.setattr(service, "open", fake_open)
        monkeypatch.setattr(routes, "get_browser_agent_service", lambda: service)

        resp = client.post("/browser/sessions", json={
            "allow_domains": ["localhost"], "allow_private": True,
        })
        assert resp.status_code == 429, resp.text
        assert captured["allow_private"] is True

    def test_one_shot_run_route(self, client, monkeypatch):
        from backend.routes import browser_sessions as routes
        from backend.modules import browser_agent

        page = FlowPage()
        seed(page)
        service = browser_agent.get_browser_agent_service()
        session = make_session(page, allow_domains=["localhost"], allow_private=True)

        async def fake_open(**kwargs):
            service._sessions[session.id] = session
            return session

        async def fake_close(session_id):
            service._sessions.pop(session_id, None)
            return {"session_id": session_id, "closed": True}

        monkeypatch.setattr(service, "open", fake_open)
        monkeypatch.setattr(service, "close", fake_close)
        monkeypatch.setattr(routes, "get_browser_agent_service", lambda: service)

        resp = client.post("/browser/sessions/run", json={
            "url": "http://localhost:3000/",
            "local": True,
            "steps": [{"action": "assert_visible", "target": "Sign in"}],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["session_id"] == session.id

    def test_invalid_flow_is_4xx(self, client):
        session = self._install()
        resp = client.post(f"/browser/sessions/{session.id}/run",
                           json={"steps": []})
        assert resp.status_code == 403
