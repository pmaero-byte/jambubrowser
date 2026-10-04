"""Determinism knobs for the browser test flow: clock, throttling, coverage.

These options exist to remove flakiness: a flow that depends on wall-clock
time, a slow network, or "did this JS ever run?" should be reproducible and
inspectable, not a coin flip.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.modules.browser_agent import (
    BrowserAgentSession,
    PlaywrightPage,
    SessionRefused,
)

from tests.test_browser_flow import FlowPage, make_session, run


# ---------------------------------------------------------------------------
# FlowPage extensions used only here
# ---------------------------------------------------------------------------

class DeterministicPage(FlowPage):
    """FlowPage that also speaks clock / coverage (and records throttle calls)."""

    class _Clock:
        def __init__(self, outer):
            self.outer = outer

        async def install(self, time=None):
            self.outer.clock_installed.append(time)

        async def pause(self):
            self.outer.clock_paused = True

        async def resume(self):
            self.outer.clock_paused = False

    class _Coverage:
        def __init__(self, outer):
            self.outer = outer
            self.started = False

        async def start_javascript_coverage(self):
            self.started = True

        async def stop_javascript_coverage(self):
            self.started = False
            return {"result": self.outer.coverage_entries}

    def __init__(self, coverage_entries=None):
        super().__init__()
        self.clock_installed: list[str] = []
        self.clock_paused = False
        self.throttle_calls: list[dict] = []
        self.coverage_entries = coverage_entries or []
        self.clock = DeterministicPage._Clock(self)
        self.coverage = DeterministicPage._Coverage(self)

    async def install_clock(self, clock: dict) -> dict:
        installed = {}
        if clock.get("time"):
            await self.clock.install(time=clock["time"])
            installed["time"] = clock["time"]
        if "rate" in clock:
            if clock["rate"] == 0:
                await self.clock.pause()
            else:
                await self.clock.resume()
            installed["rate"] = clock["rate"]
        return installed

    async def set_throttle(self, throttle: dict) -> dict:
        self.throttle_calls.append(throttle)
        return throttle

    async def start_coverage(self) -> dict:
        await self.coverage.start_javascript_coverage()
        return {"started": True}

    async def stop_coverage(self) -> dict:
        from backend.modules.browser_agent import summarise_coverage

        taken = await self.coverage.stop_javascript_coverage()
        return summarise_coverage(taken.get("result"))


STEPS = [
    {"action": "navigate", "url": "https://example.com/"},
    {"action": "assert_visible", "target": "Welcome"},
]


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------

class TestClock:
    def test_clock_is_installed_and_reported(self):
        page = DeterministicPage()
        report = run(make_session(page).run_flow(
            STEPS, clock={"time": "2026-01-01T09:00:00Z", "rate": 0},
        ))
        assert page.clock_installed == ["2026-01-01T09:00:00Z"]
        assert page.clock_paused is True
        assert report["determinism"]["clock"]["time"] == "2026-01-01T09:00:00Z"
        assert report["determinism"]["clock"]["rate"] == 0

    def test_unsupported_clock_is_reported_not_fatal(self):
        class NoClock(FlowPage):
            """Adapter without install_clock — like a non-Chromium engine."""

        report = run(make_session(NoClock()).run_flow(STEPS, clock={"rate": 0}))
        assert report["determinism"]["clock"].get("skipped")
        assert report["ok"] is True

    def test_no_clock_option_means_no_determinism_block(self):
        report = run(make_session(DeterministicPage()).run_flow(STEPS))
        assert "determinism" not in report


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

class TestThrottle:
    def test_throttle_spec_reaches_the_adapter_and_report(self):
        page = DeterministicPage()
        report = run(make_session(page).run_flow(
            STEPS, throttle={"offline": True},
        ))
        assert page.throttle_calls == [{"offline": True}]
        assert report["determinism"]["throttle"] == {"offline": True}

    def test_throttle_capability_missing_is_recorded(self):
        class NoThrottle(FlowPage):
            pass

        page = NoThrottle()
        # FlowPage has no set_throttle → _call_optional raises SessionRefused,
        # which the runner swallows into a "skipped" determinism entry.
        session = make_session(page)
        report = run(session.run_flow(STEPS, throttle={"offline": True}))
        assert report["determinism"]["throttle"].get("skipped")
        assert report["ok"] is True  # a flow still runs without shaping


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

class TestCoverage:
    @staticmethod
    def _entry(url, script_id, used, dead=()):
        """A CDP coverage entry: one function-level range for used/dead code."""
        fns = [{"ranges": [{"startOffset": 0, "endOffset": used[1], "count": 1}]}]
        fns += [{"ranges": [{"startOffset": ds, "endOffset": de, "count": 0}]}
                for ds, de in dead]
        return {"url": url, "scriptId": script_id, "functions": fns}

    def test_coverage_summary_computes_used_bytes_and_dead_zones(self):
        entries = [
            # File range covers 0..1000 and ran; the 500..700 function never did.
            self._entry("https://example.com/app.js", 1, (0, 1000), [(500, 700)]),
            # Entirely unused file.
            self._entry("https://example.com/vendor.js", 2, (0, 2000), [(0, 2000)]),
        ]
        from backend.modules.browser_agent import summarise_coverage

        out = summarise_coverage(entries)
        assert out["supported"] is True
        assert out["script_count"] == 2
        assert out["total_bytes"] == 3000
        # app.js: 1000 - 200 dead = 800; vendor.js: 0.
        assert out["used_bytes"] == 800
        # Least-used script sorts first.
        assert out["scripts"][0]["name"] == "vendor.js"
        assert out["scripts"][0]["used_bytes"] == 0

    def test_per_function_entries_are_regrouped_per_script(self):
        # Chromium emits one entry per function; same URL must merge.
        entries = [
            {"url": "https://example.com/a.js", "scriptId": 7,
             "functions": [{"ranges": [{"startOffset": 0, "endOffset": 300, "count": 1}]}]},
            {"url": "https://example.com/a.js", "scriptId": 7,
             "functions": [{"ranges": [{"startOffset": 0, "endOffset": 900, "count": 1}]}]},
        ]
        from backend.modules.browser_agent import summarise_coverage

        out = summarise_coverage(entries)
        assert out["script_count"] == 1
        assert out["scripts"][0]["total_bytes"] == 900

    def test_chrome_injected_scripts_are_excluded(self):
        entries = [self._entry("extensions::chrome://foo.js", 3, (0, 500))]
        from backend.modules.browser_agent import summarise_coverage

        out = summarise_coverage(entries)
        assert out["script_count"] == 0
        assert out["pct"] == 0.0

    def test_coverage_reaches_the_report(self):
        entries = [self._entry("https://example.com/app.js", 1, (0, 400), [(100, 400)])]
        page = DeterministicPage(coverage_entries=entries)
        report = run(make_session(page).run_flow(STEPS, coverage=True))
        cov = report["coverage"]
        assert cov["supported"] is True
        assert cov["total_bytes"] == 400
        assert cov["used_bytes"] == 100
        assert cov["pct"] == 25.0

    def test_coverage_absent_when_not_requested(self):
        report = run(make_session(DeterministicPage()).run_flow(STEPS))
        assert "coverage" not in report

    def test_coverage_unsupported_is_recorded(self):
        class NoCoverage(FlowPage):
            """Adapter without start_coverage/stop_coverage."""

        report = run(make_session(NoCoverage()).run_flow(STEPS, coverage=True))
        assert report["coverage"].get("supported") is not True

    def test_summarise_coverage_handles_empty_entries(self):
        from backend.modules.browser_agent import summarise_coverage

        out = summarise_coverage([])
        assert out["supported"] is True
        assert out["scripts"] == []
        assert out["pct"] == 0.0


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

class TestHttpSurface:
    def test_request_models_accept_the_new_knobs(self):
        from backend.routes.browser_sessions import RunFlowRequest, TestFlowRequest

        run_req = RunFlowRequest(
            steps=[], clock={"time": "2026-01-01T09:00:00Z", "rate": 0},
            throttle={"offline": True}, coverage=True,
        )
        assert run_req.coverage is True
        assert run_req.throttle == {"offline": True}
        test_req = TestFlowRequest(url="https://example.com", coverage=True,
                                   clock={"rate": 0})
        assert test_req.coverage is True
        assert test_req.clock == {"rate": 0}
