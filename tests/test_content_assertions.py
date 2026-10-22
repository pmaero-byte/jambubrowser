"""Flow-level tests for the content assertions (json ops/schema, svg, text
fidelity, prompt coverage, determinism, glyph fidelity, body).

A scripted page answers ``http_request`` from canned bodies, so every kind is
exercised through ``BrowserAgentSession.run_flow`` without a network.
"""
from __future__ import annotations

import asyncio
import json

from backend.modules.browser_agent import BrowserAgentSession

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
       '<rect id="r" x="1" y="1" width="9" height="9"/>'
       '<text id="cap" x="5" y="50">ஒரசிறமி</text></svg>')


def run(coro):
    return asyncio.run(coro)


class ContentPage:
    def __init__(self, responses: dict, dom: dict | None = None, glyph=None):
        self.url = "about:blank"
        self.responses = responses
        self.dom = dom or {}
        self.glyph = glyph
        self.calls: list = []

    async def goto(self, url: str) -> None:
        self.url = url

    async def current_url(self) -> str:
        return self.url

    async def snapshot(self) -> dict:
        return {"url": self.url, "title": "t", "elements": [], "text": ""}

    async def evaluate(self, script, arg=None):
        return self.dom.get(arg)

    async def glyph_compare(self, svg, region, expected, **kwargs):
        self.calls.append(("glyph", region, expected, kwargs.get("font_path")))
        return self.glyph

    async def http_request(self, method, url, *, headers=None, body=None, timeout_ms=15000):
        self.calls.append((method, url))
        path = url.split("example.com", 1)[1]
        canned = self.responses[path.split("?")[0]]
        if callable(canned):
            canned = canned()
        payload = canned.get("json")
        text = canned.get("text", json.dumps(payload) if payload is not None else "")
        return {"method": method, "url": url, "status": canned.get("status", 200),
                "ok": canned.get("status", 200) < 400, "latency_ms": 3,
                "json": payload, "text": text[:4000], "body": text, "headers": {}}


def flow(page, steps):
    session = BrowserAgentSession("bs-content", page, allow_domains=["example.com"],
                                  require_approval=False)
    return run(session.run_flow(
        [{"action": "navigate", "url": "https://example.com/"}] + steps))


def statuses(report):
    return [s["status"] for s in report["steps"][1:]]


class TestJsonOpsAndSchema:
    def test_ops_and_schema(self):
        page = ContentPage({"/api/caps": {"json": {"version": "0.4.0",
                                                   "formats": ["svg", "pdf"]}}})
        report = flow(page, [
            {"action": "api", "url": "https://example.com/api/caps"},
            {"action": "assert_json", "path": "version", "op": "regex",
             "expected": r"^0\.4\."},
            {"action": "assert_json", "path": "formats", "op": "contains", "expected": "pdf"},
            {"action": "assert_json_schema", "schema": {
                "type": "object", "required": ["version", "formats"],
                "properties": {"formats": {"type": "array", "items": {"type": "string"}}}}},
            {"action": "assert_json_schema", "schema": {"required": ["missing_key"]}},
        ])
        assert statuses(report) == ["passed", "passed", "passed", "passed", "failed"]
        failed = report["steps"][-1]
        assert "missing_key" in failed["error"]
        assert failed["evidence"]["schema_errors"]

    def test_unknown_op_refused_not_passed(self):
        page = ContentPage({"/api/caps": {"json": {"a": 1}}})
        report = flow(page, [{"action": "api", "url": "https://example.com/api/caps"},
                             {"action": "assert_json", "path": "a", "op": "approx",
                              "expected": 1}])
        assert report["ok"] is False
        assert report["steps"][-1]["reason"] == "invalid_step"


class TestSvgAndText:
    def test_svg_from_json_path(self):
        page = ContentPage({"/api/render": {"json": {"svg": SVG, "render_ms": 1}}})
        report = flow(page, [
            {"action": "api", "url": "https://example.com/api/render?q=x"},
            {"action": "assert_svg", "path": "svg", "checks": {
                "no_nan": True, "unique_ids": True, "no_script": True,
                "element_counts": {"text": 1}, "script": "tamil"}},
            {"action": "assert_svg", "path": "svg", "checks": {"element_counts": {"text": 2}}},
        ])
        assert statuses(report) == ["passed", "passed", "failed"]
        assert "element_counts.text" in report["steps"][-1]["error"]

    def test_text_fidelity_names_the_dropped_vowel_sign(self):
        page = ContentPage({"/api/render": {"json": {"svg": SVG}}})
        report = flow(page, [
            {"action": "api", "url": "https://example.com/api/render"},
            {"action": "assert_text_fidelity", "path": "svg", "expected": "ஒரு சிறுமி"},
        ])
        step = report["steps"][-1]
        assert step["status"] == "failed"
        assert "U+0BC1 TAMIL VOWEL SIGN U" in step["error"]
        assert "word space" in step["error"]

    def test_svg_from_live_page_selector(self):
        page = ContentPage({}, dom={"#svg-host svg": SVG})
        report = flow(page, [
            {"action": "assert_svg", "selector": "#svg-host svg",
             "checks": {"unique_ids": True}},
            {"action": "assert_svg", "selector": "#absent", "checks": {"no_nan": True}},
        ])
        assert statuses(report) == ["passed", "failed"]

    def test_body_assertions(self):
        page = ContentPage({"/api/scene/svg": {
            "text": "<svg><text>No such file or directory (os error 2)</text></svg>"}})
        report = flow(page, [
            {"action": "api", "url": "https://example.com/api/scene/svg?id=x"},
            {"action": "assert_body", "not_contains": ["No such file"]},
            {"action": "assert_body", "contains": ["<svg"]},
            {"action": "assert_body", "contains": ["<path"]},
        ])
        assert statuses(report) == ["passed", "failed", "passed", "failed"]
        # Failures say what the body does, not what was expected.
        failed = [str(s.get("error") or s.get("detail")) for s in report["steps"]
                  if s["status"] == "failed"]
        assert "contains forbidden 'No such file'" in failed[0]
        assert "lacks '<path'" in failed[1]


class TestCoverageAndDeterminism:
    def test_prompt_coverage_reads_query_from_url(self):
        page = ContentPage({"/api/prompt": {"json": {"unmatched_tokens": ["ஒரு"]}}})
        q = "ஒரு%20சிறுமி%20புத்தகம்"
        report = flow(page, [
            {"action": "api", "url": f"https://example.com/api/prompt?q={q}"},
            {"action": "assert_prompt_coverage", "min": 0.6},
            {"action": "assert_prompt_coverage", "min": 0.9},
        ])
        assert statuses(report) == ["passed", "passed", "failed"]
        assert "unmatched: ஒரு" in report["steps"][-1]["error"]

    def test_deterministic_replays_and_ignores_timings(self):
        counter = {"n": 0}

        def render():
            counter["n"] += 1
            return {"json": {"svg": "<svg/>", "render_ms": counter["n"]}}

        page = ContentPage({"/api/render": render})
        report = flow(page, [
            {"action": "api", "url": "https://example.com/api/render?q=a"},
            {"action": "assert_deterministic", "runs": 3, "ignore": ["render_ms"]},
            {"action": "assert_deterministic", "runs": 2},
        ])
        assert statuses(report) == ["passed", "passed", "failed"]
        assert counter["n"] == 1 + 2 + 1
        assert len(report["steps"][2]["evidence"]["hashes"]) == 3

    def test_deterministic_catches_a_changing_payload(self):
        counter = {"n": 0}

        def render():
            counter["n"] += 1
            return {"json": {"svg": f"<svg id='{counter['n']}'/>"}}

        page = ContentPage({"/api/render": render})
        report = flow(page, [
            {"action": "api", "url": "https://example.com/api/render"},
            {"action": "assert_deterministic", "runs": 3, "path": "svg"},
        ])
        assert report["steps"][-1]["status"] == "failed"
        assert "3 distinct" in report["steps"][-1]["error"]


class TestGlyphFidelity:
    def _metrics(self, coverage, window, gaps=(1, 1)):
        return {"metrics": {"coverage": coverage, "min_window_coverage": window,
                            "aspect_ratio_delta": 0.01, "word_gaps_actual": gaps[0],
                            "word_gaps_reference": gaps[1], "similarity": 0.9,
                            "precision": 0.9}}

    def test_pass_and_fail_use_calibrated_thresholds(self):
        for glyph, expected in ((self._metrics(0.99, 0.9), "passed"),
                                (self._metrics(0.70, 0.16, (0, 1)), "failed")):
            page = ContentPage({"/api/render": {"json": {"svg": SVG}}}, glyph=glyph)
            report = flow(page, [
                {"action": "api", "url": "https://example.com/api/render"},
                {"action": "assert_glyph_fidelity", "path": "svg", "region": "#cap",
                 "expected": "ஒரு சிறுமி", "font": "/fonts/NotoSansTamil.ttf"},
            ])
            step = report["steps"][-1]
            assert step["status"] == expected, step
            assert page.calls[-1] == ("glyph", "#cap", "ஒரு சிறுமி",
                                      "/fonts/NotoSansTamil.ttf")
        assert "word gap" in step["error"]

    def test_adapter_error_fails(self):
        page = ContentPage({"/api/render": {"json": {"svg": SVG}}},
                           glyph={"error": "no visible element matches '#x'"})
        report = flow(page, [
            {"action": "api", "url": "https://example.com/api/render"},
            {"action": "assert_glyph_fidelity", "path": "svg", "region": "#x",
             "expected": "a"},
        ])
        assert report["steps"][-1]["status"] == "failed"


class ShotPage(ContentPage):
    def __init__(self, shots):
        super().__init__({})
        self.shots = shots
        self.kwargs = None

    async def diff_screenshot(self, name, selector="", threshold=0.005, masks=None, **kw):
        self.kwargs = kw
        return dict(self.shots)


class TestScreenshotThreshold:
    def test_percent_vs_fraction(self):
        # diff_pct is a percentage; threshold is a fraction. 0.3% differing is
        # inside the 0.5% default (it used to fail: 0.3 > 0.005).
        page = ShotPage({"diff_pct": 0.3, "diff_pixels": 30})
        report = flow(page, [{"action": "assert_screenshot", "name": "home",
                              "baseline_dir": "/tmp/pack-baselines"}])
        assert report["steps"][-1]["status"] == "passed", report["steps"][-1]
        assert page.kwargs["baseline_dir"] == "/tmp/pack-baselines"
        page = ShotPage({"diff_pct": 0.8, "diff_pixels": 80})
        report = flow(page, [{"action": "assert_screenshot", "name": "home"}])
        assert report["steps"][-1]["status"] == "failed"

    def test_update_mode_is_reported(self):
        page = ShotPage({"baseline_updated": True})
        report = flow(page, [{"action": "assert_screenshot", "name": "home",
                              "update_baseline": True}])
        assert "rewritten" in report["steps"][-1]["detail"]
        assert page.kwargs["update"] is True

    def test_size_mismatch_is_named_and_normalize_width_is_passed(self):
        page = ShotPage({"size_mismatch": True, "diff_pct": 100.0,
                         "baseline_size": [782, 1107], "current_size": [521, 738]})
        report = flow(page, [{"action": "assert_screenshot", "name": "plate",
                              "normalize_width": 130}])
        step = report["steps"][-1]
        assert step["status"] == "failed"
        assert "[521, 738]" in str(step.get("error") or step.get("detail"))
        assert page.kwargs["normalize_width"] == 130
        # Adapters that predate the option are not handed an unknown keyword.
        flow(page, [{"action": "assert_screenshot", "name": "plate"}])
        assert "normalize_width" not in page.kwargs
