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
        self.console_locations: list[dict] = []
        self.page_errors = []
        self.failed_requests = []
        self.network_setups: list[dict] = []
        self.injected_css: list[str] = []
        self.network_requests: list[str] = []
        self.a11y = {"issues": [], "total": 0}
        self.perf = {
            "lcp_ms": 1200, "fcp_ms": 400, "load_ms": 1500, "response_ms": 100,
            "dom_nodes": 300, "resource_count": 20, "transfer_bytes": 200 * 1024,
        }
        self.source_maps: dict[str, str] = {}
        self.selector_actions: list[tuple] = []
        self.eval_scripts: list[str] = []
        self.dom: dict[str, dict] = {
            "#submit": {"visible": True, "text": "Submit", "value": "",
                        "checked": False, "enabled": True, "count": 1},
            "#hidden-note": {"visible": False, "text": "secret", "count": 1},
            ".item": {"visible": True, "text": "item", "count": 3},
        }

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
        if target.get("console_error"):
            self.console.append(target["console_error"])
            self.console_locations.append(target.get("console_location") or {})
        if target.get("request"):
            self.network_requests.append(target["request"])

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
        detail = []
        for i, text in enumerate(self.console):
            loc = self.console_locations[i] if i < len(self.console_locations) else {}
            detail.append({"text": text, "location": loc})
        detail += [{"text": e, "location": {}} for e in self.page_errors]
        return {
            "console_errors": list(self.console) + list(self.page_errors),
            "console_errors_detail": detail,
            "console_warnings": [],
            "failed_requests": list(self.failed_requests),
            "bad_responses": [],
        }

    def drain_telemetry(self) -> dict:
        data = self.peek_telemetry()
        self.console.clear()
        self.console_locations.clear()
        self.failed_requests.clear()
        return data

    async def setup_network(self, network: dict) -> dict:
        self.network_setups.append(network)
        rules = len(network.get("mocks") or []) + len(network.get("fail") or []) \
            + len(network.get("delay") or [])
        return {"rules": rules, "offline": bool(network.get("offline"))}

    async def inject_css(self, css: str) -> None:
        self.injected_css.append(css)

    async def a11y_audit(self) -> dict:
        return self.a11y

    async def perf_metrics(self) -> dict:
        return self.perf

    def made_request(self, pattern: str) -> bool:
        return any(pattern in url for url in self.network_requests)

    async def evaluate(self, script: str, arg=None):
        return None

    async def fetch_text(self, url: str):
        return self.source_maps.get(url)

    async def resource_count(self) -> int:
        return len(self.network_requests)

    async def click_selector(self, selector: str) -> None:
        self.selector_actions.append(("click", selector, ""))

    async def fill_selector(self, selector: str, text: str) -> None:
        self.selector_actions.append(("fill", selector, text))

    async def press_selector(self, selector: str, key: str) -> None:
        self.selector_actions.append(("press", selector, key))

    async def hover_selector(self, selector: str) -> None:
        self.selector_actions.append(("hover", selector, ""))

    async def select_selector(self, selector: str, value: str) -> None:
        self.selector_actions.append(("select", selector, value))

    async def check_selector(self, selector: str, checked: bool = True) -> None:
        self.selector_actions.append(("check", selector, checked))

    async def is_visible_selector(self, selector: str) -> bool:
        return self.dom.get(selector, {}).get("visible", False)

    async def text_of_selector(self, selector: str) -> str:
        return self.dom.get(selector, {}).get("text", "")

    async def value_of_selector(self, selector: str) -> str:
        return self.dom.get(selector, {}).get("value", "")

    async def count_selector(self, selector: str) -> int:
        return self.dom.get(selector, {}).get("count", 0)

    async def is_enabled_selector(self, selector: str) -> bool:
        return self.dom.get(selector, {}).get("enabled", False)

    async def is_checked_selector(self, selector: str) -> bool:
        return self.dom.get(selector, {}).get("checked", False)

    async def eval_js(self, script: str):
        self.eval_scripts.append(script)
        return f"result-of:{script[:16]}"


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
# Debugging: network, cause attribution, a11y/perf, source maps
# ---------------------------------------------------------------------------

class TestNetworkAndDebug:
    def test_network_policy_is_installed_and_reported(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow(
            [{"action": "navigate", "url": "https://example.com/"}],
            network={
                "mocks": [{"url": "**/api/user", "json": {"name": "T"}}],
                "fail": ["**/analytics/**"],
                "delay": [{"url": "**/slow", "ms": 50}],
                "offline": False,
            },
        ))
        assert page.network_setups, "network policy should be installed"
        assert report["network"]["rules"] == 3

    def test_freeze_animations_injects_css(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        run(session.run_flow([{"action": "navigate", "url": "https://example.com/"}]))
        assert page.injected_css and "animation:none" in page.injected_css[0]

    def test_animations_can_be_left_alone(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        run(session.run_flow(
            [{"action": "navigate", "url": "https://example.com/"}],
            freeze_animations=False,
        ))
        assert page.injected_css == []

    def test_cause_attribution_records_dom_and_errors(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
            {"action": "click", "target": "Home"},  # navigates + reveals Dashboard
        ]))
        # The click step should carry what it changed (Dashboard appeared).
        click_step = report["steps"][1]
        assert "cause" in click_step
        assert click_step["cause"]["dom"]["added"] >= 1

    def test_a11y_assertion_reports_violations(self):
        page = FlowPage()
        seed(page)
        page.a11y = {"issues": [{"id": "image-alt", "count": 2}], "total": 2}
        session = make_session(page)
        report = run(session.run_flow([{"action": "assert_no_a11y_violations"}]))
        assert report["ok"] is False
        assert "a11y" in report["steps"][0]["error"]

    def test_a11y_assertion_passes_clean(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([{"action": "assert_no_a11y_violations"}]))
        assert report["ok"] is True

    def test_perf_budget_assertions(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "assert_lcp", "value": 2500},
            {"action": "assert_fcp", "value": 500},
            {"action": "assert_dom_nodes", "value": 1000},
            {"action": "assert_transfer_kb", "value": 500},
        ]))
        assert report["ok"] is True

    def test_perf_budget_failure(self):
        page = FlowPage()
        seed(page)
        page.perf["lcp_ms"] = 9000
        session = make_session(page)
        report = run(session.run_flow([{"action": "assert_lcp", "value": 2500}]))
        assert report["ok"] is False

    def test_request_assertions(self):
        page = FlowPage()
        seed(page)
        page.network_requests = ["https://example.com/api/order"]
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "assert_made_request", "value": "/api/order"},
            {"action": "assert_no_request", "value": "/api/analytics"},
        ]))
        assert report["ok"] is True

    def test_settle_waits_for_network_quiet(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow(
            [{"action": "navigate", "url": "https://example.com/"}],
            settle_ms=100,
        ))
        assert report["ok"] is True

    def test_resolve_sources_maps_console_errors(self):
        page = FlowPage()
        # Register a source map for the bundle the error points into.
        page.source_maps["https://example.com/app.js.map"] = json.dumps({
            "version": 3, "sources": ["src/App.tsx"], "mappings": "AAAA",
        })
        page.console = ["TypeError: boom"]
        page.console_locations = [{
            "url": "https://example.com/app.js", "lineNumber": 0, "columnNumber": 0,
        }]
        session = make_session(page)
        page.network_requests = []
        report = run(session.run_flow(
            [{"action": "navigate", "url": "https://example.com/"}],
            resolve_sources=True,
        ))
        mapped = report["console_errors_source"]
        assert mapped[0]["source"] == "src/App.tsx"
        assert mapped[0]["source_line"] == 1


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


class TestMatrix:
    def test_matrix_aggregates_variants(self, monkeypatch):
        service = BrowserAgentService()

        async def fake_run_test(**kwargs):
            width = (kwargs.get("context_options") or {}).get("viewport", {}).get("width")
            return {"ok": width == 1280, "passed": 1, "failed": 0, "total": 1,
                    "steps": [], "console_errors": []}

        monkeypatch.setattr(service, "run_test", fake_run_test)
        result = run(service.run_matrix(
            url="http://x", steps=[{"action": "navigate", "url": "http://x"}],
            local=True,
        ))
        assert result["summary"]["variants"] == 2
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1
        assert result["ok"] is False
        assert result["variants"][0]["variant"] == "desktop"

    def test_matrix_custom_and_failure_isolation(self, monkeypatch):
        service = BrowserAgentService()

        async def fake_run_test(**kwargs):
            opts = kwargs.get("context_options") or {}
            if opts.get("locale") == "fr-FR":
                raise RuntimeError("boom")
            return {"ok": True, "passed": 2, "failed": 0, "total": 2,
                    "steps": [], "console_errors": []}

        monkeypatch.setattr(service, "run_test", fake_run_test)
        result = run(service.run_matrix(
            url="http://x", steps=[{"action": "navigate", "url": "http://x"}],
            matrix=[{"name": "en", "locale": "en-US"}, {"name": "fr", "locale": "fr-FR"}],
        ))
        assert result["variants"][0]["ok"] is True
        assert result["variants"][1]["ok"] is False
        assert "boom" in result["variants"][1]["error"]


class TestArtifactsAndContext:
    def test_open_builds_context_options_and_artifacts(self, monkeypatch, tmp_path):
        import backend.modules.browser as browser_mod

        captured = {}

        class FakeBrowserSession:
            async def get_page(self):
                return object()

            async def start_trace(self):
                captured["trace_started"] = True

            async def stop_trace(self, path):
                with open(path, "w") as fh:
                    fh.write("zip")
                return path

            async def stop(self):
                captured["stopped"] = True

        class FakeManager:
            @classmethod
            def get_instance(cls):
                return cls()

            async def get_session(self, sid, mode=None, privacy_level=None,
                                  context_options=None):
                captured["context_options"] = context_options
                return FakeBrowserSession()

        monkeypatch.setattr(browser_mod.BrowserManager, "get_instance",
                            FakeManager.get_instance)

        service = BrowserAgentService()
        session = run(service.open(
            allow_domains=["localhost"], allow_private=True,
            storage_state={"cookies": [], "origins": []},
            trace=True, har=True, artifacts_dir=str(tmp_path),
        ))
        assert captured["context_options"]["storage_state"] == {"cookies": [], "origins": []}
        assert captured["context_options"]["record_har_path"].endswith("network.har")
        assert captured["context_options"]["service_workers"] == "block"
        assert captured["trace_started"] is True

        result = run(service.close(session.id))
        assert captured["stopped"] is True
        assert result["artifacts"]["trace"].endswith("trace.zip")


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


class TestLiveView:
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

    def test_screenshot_endpoint(self, client):
        session = self._install()
        resp = client.get(f"/browser/sessions/{session.id}/screenshot")
        assert resp.status_code == 200
        assert resp.json()["screenshot_base64"] == "QUJD"

    def test_takeover_toggle(self, client):
        session = self._install()
        resp = client.post(f"/browser/sessions/{session.id}/takeover",
                           json={"active": True})
        assert resp.status_code == 200
        assert resp.json()["human_takeover"] is True
        receipts = client.get(f"/browser/sessions/{session.id}/receipts").json()
        assert receipts["human_takeover"] is True

    def test_screenshot_unsupported_session(self, client):

        class NoShotPage:
            url = "about:blank"

            async def goto(self, url):
                self.url = url

            async def snapshot(self):
                return {"url": self.url, "title": "", "elements": [], "text": ""}

            async def click(self, ref):
                pass

            async def type_text(self, ref, text):
                pass

            async def current_url(self):
                return self.url

        session = BrowserAgentSession("bs-noshot", NoShotPage(),
                                      allow_domains=["example.com"])
        from backend.modules import browser_agent
        browser_agent.get_browser_agent_service()._sessions[session.id] = session
        resp = client.get(f"/browser/sessions/{session.id}/screenshot")
        assert resp.status_code == 501


class TestRecording:
    def test_session_captures_actions(self):
        page = FlowPage()
        seed(page)
        page.elements.append(
            {"ref": "@e7", "tag": "input", "role": "", "type": "password",
             "name": "Password", "href": "", "visible": True, "value": ""}
        )
        session = make_session(page)
        session.start_recording()
        run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
            {"action": "type", "target": "Email", "value": "dev@example.com"},
            {"action": "type", "target": "Password", "value": "hunter2"},
            {"action": "click", "target": "Home"},
        ]))
        result = session.stop_recording()
        actions = [s["action"] for s in result["steps"]]
        assert actions == ["navigate", "type", "type", "click"]
        # Credentials become replayable placeholders, never recorded verbatim.
        assert result["steps"][2]["value"] == "{{password}}"
        assert result["steps"][1]["value"] == "{{email}}"

    def test_recording_route(self):
        from fastapi.testclient import TestClient
        from backend.engine import app
        from backend.modules import browser_agent

        browser_agent.reset_browser_agent_service()
        page = FlowPage()
        seed(page)
        session = make_session(page)
        browser_agent.get_browser_agent_service()._sessions[session.id] = session

        with TestClient(app) as client:
            started = client.post(f"/browser/sessions/{session.id}/record",
                                  json={"active": True})
            assert started.status_code == 200
            run(session.run_flow([
                {"action": "navigate", "url": "https://example.com/"},
            ]))
            flow = client.get(f"/browser/sessions/{session.id}/flow").json()
            assert flow["count"] == 1
            assert flow["steps"][0]["action"] == "navigate"
            stopped = client.post(f"/browser/sessions/{session.id}/record",
                                  json={"active": False}).json()
            assert stopped["recording"] is False
        browser_agent.reset_browser_agent_service()


class TestSelectors:
    def test_click_and_type_by_selector(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "click", "selector": "#submit", "approve": True},
            {"action": "type", "selector": "#email", "value": "a@b.com", "approve": True},
        ]))
        assert report["ok"] is True
        assert ("click", "#submit", "") in page.selector_actions
        assert ("fill", "#email", "a@b.com") in page.selector_actions

    def test_selector_requires_approval(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([{"action": "click", "selector": "#submit"}]))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "approval_required"
        assert page.selector_actions == []

    def test_selector_needs_address(self):
        page = FlowPage()
        session = make_session(page)
        report = run(session.run_flow([{"action": "click"}]))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "target_required"

    def test_selector_asserts(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "assert_visible", "selector": "#submit"},
            {"action": "assert_not_visible", "selector": "#hidden-note"},
            {"action": "assert_text", "selector": "#submit", "value": "Sub"},
            {"action": "assert_count", "selector": ".item", "value": 3},
            {"action": "assert_enabled", "selector": "#submit"},
        ]))
        assert report["ok"] is True

    def test_selector_assert_failure(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "assert_visible", "selector": "#missing"},
        ]))
        assert report["ok"] is False

    def test_evaluate_requires_approval(self):
        page = FlowPage()
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "evaluate", "script": "document.title"},
        ]))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "approval_required"

    def test_evaluate_runs_with_approval(self):
        page = FlowPage()
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "evaluate", "script": "document.title", "approve": True},
        ]))
        assert report["ok"] is True
        assert page.eval_scripts == ["document.title"]
        assert "result-of" in report["steps"][0]["evaluated"]

    def test_takeover_blocks_selector_actions(self):
        page = FlowPage()
        session = make_session(page)
        session.human_takeover = True
        with pytest.raises(SessionRefused) as exc:
            run(session.act_selector("click", "#submit", approve=True))
        assert exc.value.reason == "human_takeover"

    def test_act_route_accepts_selector(self):
        page = FlowPage()
        session = make_session(page)
        result = run(session.act("click", "", approve=True, selector="#submit"))
        assert result["outcome"] == "ok"
        assert ("click", "#submit", "") in page.selector_actions


class TestTakeoverEnforced:
    def test_act_refused_under_human_control(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        run(session._read_state())
        session.human_takeover = True
        with pytest.raises(SessionRefused) as exc:
            run(session.act("click", "@e1", approve=True))
        assert exc.value.reason == "human_takeover"
        assert page.clicks == []

    def test_flow_mutations_refused_but_observation_allowed(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        session.human_takeover = True
        report = run(session.run_flow([
            {"action": "assert_visible", "target": "Sign in"},
            {"action": "click", "target": "Sign in"},
        ]))
        assert report["passed"] == 1 and report["failed"] == 1
        assert report["steps"][1]["reason"] == "human_takeover"

    def test_clearing_takeover_restores_control(self):
        page = FlowPage()
        seed(page)
        session = make_session(page)
        session.human_takeover = True
        session.human_takeover = False
        run(session._read_state())
        result = run(session.act("click", "@e1", approve=True))
        assert result["outcome"] == "ok"


class TestTokensAndCoverage:
    def test_report_carries_token_estimate(self):
        session = make_session()
        report = run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
        ]))
        assert report["tokens_estimate"] > 0
        assert f"~{report['tokens_estimate']} tokens" in render_flow_report(report)

    def test_uses_evaluate_flag(self):
        session = make_session()
        report = run(session.run_flow([
            {"action": "evaluate", "script": "1+1", "approve": True},
        ]))
        assert report["ok"] is True
        assert report["uses_evaluate"] is True
        assert "uses JS evaluate" in render_flow_report(report)

    def test_forbid_evaluate_refuses(self):
        session = make_session()
        report = run(session.run_flow(
            [{"action": "evaluate", "script": "1+1", "approve": True}],
            forbid_evaluate=True,
        ))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "evaluate_forbidden"
        assert report["uses_evaluate"] is True

    def test_estimate_tokens_helper(self):
        from backend.modules.browser_agent import estimate_tokens

        assert estimate_tokens("x" * 400) == 100
        assert estimate_tokens({}) >= 1
