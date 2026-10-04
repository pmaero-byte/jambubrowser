"""The audit collection phases: order, isolation, and the pure helpers.

`collect_page_data` used to be one 268-line function. The phases now live in
`backend/routes/audit_collect.py` and are called in a fixed order, which makes
two properties worth pinning that nothing else checked:

* **Order matters.** Listeners must be attached before navigation or the initial
  document response is missed, and detached before the context closes or a
  listener fires into a dead page.
* **A failed phase does not fail the audit.** Every phase keeps its own
  try/except; a missing screenshot must not turn a whole audit into a 500.

These run against fakes rather than a browser: the phases only use `page.goto`,
`page.screenshot`, `page.accessibility`, `page.content` and `page.evaluate`, so a
scripted page exercises the real control flow.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone

import pytest

from backend.employees.base import AuditData
from backend.routes import audit_collect


def run(coro):
    return asyncio.run(coro)


class FakeRequest:
    """Stands in for AuditCollectRequest — the phases only read these fields."""

    url = "https://example.com/"
    width = 1440
    height = 900
    timeout_ms = 30000
    capture_screenshot = True
    capture_fullpage = False


class FakeTiming(dict):
    pass


class FakeRequestInfo:
    url = "https://example.com/"
    method = "GET"
    resource_type = "document"
    timing = FakeTiming(startTime=0, dnsStart=1, dnsEnd=5, connectStart=5,
                         connectEnd=20, sendEnd=25, receiveHeadersEnd=60,
                         responseEnd=100)


class FakeHeaders(dict):
    pass


class FakeResponse:
    status = 200
    status_text = "OK"
    headers = FakeHeaders({"content-length": "1234"})

    def __init__(self):
        self.request = FakeRequestInfo()


class FakePage:
    """Scripted page: records calls, and fails whichever phase you name."""

    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()
        self.calls: list[str] = []
        self._listeners: list[tuple[str, object]] = []

    def on(self, event, handler):
        self._listeners.append((event, handler))

    def remove_listener(self, event, handler):
        before = len(self._listeners)
        self._listeners = [l for l in self._listeners if l != (event, handler)]
        assert len(self._listeners) < before, "remove_listener dropped nothing"

    async def goto(self, url, wait_until=None, timeout=None):
        self.calls.append("goto")
        if "goto" in self.fail:
            raise RuntimeError("net::ERR_CONNECTION_REFUSED")
        return FakeResponse()

    async def title(self):
        return "Example Domain"

    async def screenshot(self, type=None, full_page=False):
        self.calls.append("screenshot")
        if "screenshot" in self.fail:
            raise RuntimeError("screenshot failed")
        return b"\x89PNG-fake"

    async def content(self):
        self.calls.append("content")
        if "content" in self.fail:
            raise RuntimeError("content failed")
        return "<html><body>hi</body></html>"

    async def evaluate(self, script):
        self.calls.append("evaluate")
        if "evaluate" in self.fail:
            raise RuntimeError("evaluate failed")
        if "performance" in script:
            return {"fcp": 900, "fp": 700, "lcp": 1500, "cls": 0.02,
                    "dom_content_loaded": 800, "load_complete": 1000,
                    "ttfb": 120, "dom_nodes": 210}
        return {"totalNodes": 210, "lang": "en", "headings": ["H1: Example"],
                "links": [], "buttons": [], "inputs": [], "images": [],
                "forms": [], "meta": []}

    class _A11y:
        async def snapshot(inner):
            return None            # forces the DOM fallback path

    accessibility = _A11y()


class FakeContext:
    def __init__(self):
        self.cookies_called = 0

    async def cookies(self):
        self.cookies_called += 1
        return [{"name": "sid", "value": "abc"}]

    async def close(self):
        self.cookies_called = -1


def fire(page: FakePage, event: str, **kwargs):
    """Invoke every listener registered for an event, as Playwright would."""
    for name, handler in list(page._listeners):
        if name == event:
            run(handler(**kwargs))


# ---------------------------------------------------------------------------
# Collector lifecycle
# ---------------------------------------------------------------------------

class TestCollectors:
    def test_attach_then_detach_leaves_no_listeners(self):
        page = FakePage()
        capture = run(audit_collect.attach_collectors(page, FakeContext()))
        assert len(page._listeners) == 3
        assert capture.detach is not None
        capture.detach()
        assert page._listeners == []

    def test_each_page_gets_its_own_capture(self):
        """Two collects must not share counters."""
        first = run(audit_collect.attach_collectors(FakePage(), FakeContext()))
        second = run(audit_collect.attach_collectors(FakePage(), FakeContext()))
        assert first is not second
        assert first.requests is not second.requests


class TestNavigation:
    def test_navigation_records_title_viewport_and_load_time(self):
        page = FakePage()
        data = AuditData(url="https://example.com/",
                         collected_at=datetime.now(timezone.utc).isoformat())
        capture = run(audit_collect.attach_collectors(page, FakeContext()))
        run(audit_collect.navigate_and_time(page, data, FakeRequest(), capture, 0.0))

        assert page.calls == ["goto"]
        assert data.title == "Example Domain"
        assert data.viewport_width == 1440
        assert data.viewport_height == 900
        assert data.load_time_ms >= 0

    def test_a_failed_navigation_still_yields_an_audit(self):
        """A refused connection must not abort the collection."""
        page = FakePage(fail={"goto"})
        data = AuditData(url="https://example.com/",
                         collected_at=datetime.now(timezone.utc).isoformat())
        capture = run(audit_collect.attach_collectors(page, FakeContext()))
        run(audit_collect.navigate_and_time(page, data, FakeRequest(), capture, 0.0))

        assert data.viewport_width == 1440       # set regardless
        assert data.load_time_ms >= 0


# ---------------------------------------------------------------------------
# Phases are individually best-effort
# ---------------------------------------------------------------------------

class TestPhaseIsolation:
    """Each phase logs and continues; the audit is still returned."""

    def _run_all_phases(self, fail: set[str]):
        page = FakePage(fail=fail)
        data = AuditData(url="https://example.com/",
                         collected_at=datetime.now(timezone.utc).isoformat())
        capture = run(audit_collect.attach_collectors(page, FakeContext()))
        run(audit_collect.navigate_and_time(page, data, FakeRequest(), capture, 0.0))
        run(audit_collect.collect_screenshots(page, data, FakeRequest()))
        run(audit_collect.collect_dom_snapshot(page, data))
        run(audit_collect.collect_page_source(page, data))
        audit_collect.attach_network_to_data(data, capture)
        run(audit_collect.collect_performance(page, data))
        return page, data, capture

    def test_all_phases_populate_the_record(self):
        page, data, capture = self._run_all_phases(set())
        assert data.screenshot_base64 == base64.b64encode(b"\x89PNG-fake").decode()
        assert "DOM nodes: 210" in data.dom_snapshot
        assert data.page_source.startswith("<html>")
        assert data.lighthouse_report["categories"]["performance"]["score"] > 0
        assert data.network_requests == []
        assert capture.detach is not None

    def test_screenshot_failure_does_not_stop_the_other_phases(self):
        page, data, _capture = self._run_all_phases({"screenshot"})
        assert data.screenshot_base64 is None      # left unset, not faked
        assert data.page_source.startswith("<html>")      # later phase still ran
        assert "evaluate" in page.calls                   # perf ran too

    def test_performance_failure_leaves_the_rest_intact(self):
        page, data, _capture = self._run_all_phases({"evaluate"})
        assert data.lighthouse_report is None
        assert data.page_source.startswith("<html>")
        # The DOM phase used the same evaluate() and failed too; the point is
        # that neither failure took down the collection.
        assert data.dom_snapshot is None


# ---------------------------------------------------------------------------
# Pure helpers (no browser needed)
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_accessibility_tree_renders_roles_and_names(self):
        tree = {
            "role": "WebArea",
            "name": "Example Domain",
            "children": [{"role": "link", "name": "More information",
                          "children": []}],
        }
        out = audit_collect._format_accessibility_tree(tree)
        assert "WebArea" in out and "Example Domain" in out
        assert "link" in out and "More information" in out

    def test_dom_fallback_states_its_counts(self):
        out = audit_collect._format_dom_fallback({
            "totalNodes": 210, "lang": "en",
            "headings": ["H1: Example"], "links": [], "buttons": [],
            "inputs": [], "images": [], "forms": [], "meta": [],
        })
        assert "DOM nodes: 210" in out
        assert "lang=en" in out
        assert "H1: Example" in out

    def test_score_metric_is_one_for_the_best_value(self):
        # A value inside the "good" threshold scores 1.0.
        assert audit_collect._score_metric(900, [1800, 3000]) == 1.0

    def test_score_metric_is_zero_past_the_bad_threshold(self):
        assert audit_collect._score_metric(5000, [1800, 3000]) == 0.0

    def test_lower_is_better_flips_the_scoring(self):
        # CLS: 0.0 is perfect, 0.5 is terrible.
        assert audit_collect._score_metric(0.0, [0.1, 0.25], lower_is_better=True) == 1.0
        assert audit_collect._score_metric(0.5, [0.1, 0.25], lower_is_better=True) == 0.0

    def test_unknown_metrics_score_zero_not_one(self):
        """A missing metric must not read as a perfect score."""
        assert audit_collect._score_metric(None, [1800, 3000]) == 0.0
        assert audit_collect._estimate_perf_score({}) == 0.0

    def test_perf_score_is_a_zero_to_one_fraction(self):
        fast = {"fcp": 700, "lcp": 1200, "ttfb": 80, "dom_nodes": 100, "cls": 0.0}
        slow = {"fcp": 4000, "lcp": 6000, "ttfb": 2000, "dom_nodes": 4000,
                "cls": 0.4}
        assert 0.0 <= audit_collect._estimate_perf_score(fast) <= 1.0
        assert audit_collect._estimate_perf_score(fast) > audit_collect._estimate_perf_score(slow)


class TestCaptureRecord:
    def test_detach_defaults_to_none_so_teardown_can_check(self):
        assert audit_collect.NetworkCapture().detach is None

    def test_fresh_captures_share_nothing(self):
        a, b = audit_collect.NetworkCapture(), audit_collect.NetworkCapture()
        a.requests.append({"url": "x"})
        assert b.requests == []