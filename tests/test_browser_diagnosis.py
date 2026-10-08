"""Failure diagnosis against a real layout.

``DESCRIBE_SELECTOR_JS`` answers "was this element there, and could I have hit
it?" -- the question a Playwright call log leaves unanswered. These run against
an actual browser because the answers depend on real geometry and real
hit-testing; a mock would only prove the mock.

Skipped when no Chromium is installed, so the unit suite stays hermetic.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.modules.browser_page import DESCRIBE_SELECTOR_JS, SUGGEST_SELECTORS_JS

playwright_api = pytest.importorskip("playwright.async_api")

PAGE_HTML = """
<style>
  body { margin: 0; font: 14px sans-serif; }
  #under  { position: absolute; left: 40px;  top: 60px; width: 200px; height: 50px; }
  #over   { position: absolute; left: 40px;  top: 60px; width: 200px; height: 50px;
            background: #333; color: #fff; z-index: 9; }
  #far    { position: absolute; left: 4000px; top: 10px; }
  #hidden { display: none; }
  #faded  { visibility: hidden; }
</style>
<button id="under" data-testid="run-safe">Run safe</button>
<div id="over">Accept cookies</div>
<button id="far">Off screen</button>
<button id="dis" disabled>Delete</button>
<button id="hidden">Hidden</button>
<button id="faded">Faded</button>
<input id="email" name="email" value="a@b.com">
"""


def run_page(body):
    """Load PAGE_HTML, run *body* against the live page, return its result."""
    async def go():
        async with playwright_api.async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 800, "height": 600})
                await page.set_content(PAGE_HTML)
                return await body(page)
            finally:
                await browser.close()
    return asyncio.run(go())


def describe(page, selector):
    return page.evaluate(DESCRIBE_SELECTOR_JS, selector)


def have_chromium() -> bool:
    """Whether a real browser can be launched, so the suite can skip cleanly.

    Deliberately re-raises anything that is not a launch failure: a bare
    ``except`` here once turned a typo in this file into a silent "no Chromium"
    and skipped the whole module, which is precisely the class of bug this
    suite exists to catch.
    """
    async def check():
        async with playwright_api.async_playwright() as pw:
            browser = await pw.chromium.launch()
            await browser.close()
            return True

    try:
        return asyncio.run(check())
    except Exception as exc:  # noqa: BLE001 - the point is to inspect it
        if not _looks_like_missing_browser(exc):
            raise
        return False


def _looks_like_missing_browser(exc: Exception) -> bool:
    text = str(exc).lower()
    return "executable doesn't exist" in text or "please run" in text


pytestmark = pytest.mark.skipif(
    not have_chromium(), reason="no Chromium available for layout tests"
)


class TestDescribeSelector:
    def test_a_missing_element_is_reported_as_missing(self):
        result = run_page(lambda p: describe(p, "#nope"))
        assert result["found"] is False
        assert "likely_cause" not in result

    def test_an_element_under_an_overlay_says_so(self):
        # The single most useful answer: the button is there, enabled and on
        # screen -- something else is on top of it.
        result = run_page(lambda p: describe(p, "#under"))
        assert result["found"] is True
        assert result["enabled"] is True
        assert result["in_viewport"] is True
        assert "on top" in result["likely_cause"]
        assert result["covered_by"]["selector"] == "div#over"
        assert result["covered_by"]["text"] == "Accept cookies"

    def test_an_off_screen_element_says_where_it_is(self):
        # The phone-width case: laid out, not hidden, never clickable because
        # its centre is below the fold.
        result = run_page(lambda p: describe(p, "#far"))
        assert result["found"] is True
        assert result["displayed"] is True
        assert result["in_viewport"] is False
        assert result["off_screen"] is True
        assert result["covered_by"] is None
        assert result["rect"]["x"] == 4000
        assert result["viewport"] == {"width": 800, "height": 600}
        assert "outside" in result["likely_cause"]

    def test_a_disabled_element_is_reported_as_disabled(self):
        result = run_page(lambda p: describe(p, "#dis"))
        assert result["enabled"] is False
        assert "disabled" in result["likely_cause"]

    def test_display_none_is_distinguished_from_off_screen(self):
        result = run_page(lambda p: describe(p, "#hidden"))
        assert result["displayed"] is False
        assert result["hidden_by_css"] == "display:none"
        assert "not rendered" in result["likely_cause"]

    def test_visibility_hidden_is_reported_too(self):
        result = run_page(lambda p: describe(p, "#faded"))
        assert result["displayed"] is False
        assert result["hidden_by_css"] == "visibility:hidden"

    def test_a_healthy_element_says_it_is_clickable(self):
        result = run_page(lambda p: describe(p, "#email"))
        assert result["found"] is True
        assert result["enabled"] is True
        assert result["in_viewport"] is True
        assert result["covered_by"] is None
        assert "looks clickable" in result["likely_cause"]

    def test_an_element_containing_another_is_not_reported_as_covered(self):
        # A button wrapping an icon must not accuse its own child.
        async def body(page):
            await page.set_content(
                '<div id="wrap" style="position:absolute;left:5px;top:5px;width:80px;height:40px">'
                '<button id="wrap-btn"><span id="icon">x</span></button></div>'
            )
            return await describe(page, "#wrap-btn")
        result = run_page(body)
        assert result["covered_by"] is None

    def test_a_bad_css_selector_is_reported_not_raised(self):
        result = run_page(lambda p: describe(p, "::::bad"))
        assert result["found"] is False
        assert "bad selector" in result.get("error", "")

    def test_an_xpath_is_accepted(self):
        result = run_page(lambda p: describe(p, "xpath=//button[@id='dis']"))
        assert result["found"] is True
        assert result["matched_via"] == "xpath"
        assert result["enabled"] is False


class TestSuggestSelectors:
    def test_a_testid_is_preferred(self):
        # data-testid is the only spelling that survives a redesign, so it comes
        # first in the suggestion list.
        result = run_page(lambda p: p.evaluate(SUGGEST_SELECTORS_JS, "#under"))
        assert result[0] == '[data-testid="run-safe"]'

    def test_an_id_is_suggested_when_there_is_no_testid(self):
        result = run_page(lambda p: p.evaluate(SUGGEST_SELECTORS_JS, "#dis"))
        assert "#dis" in result

    def test_at_most_three_suggestions(self):
        result = run_page(lambda p: p.evaluate(SUGGEST_SELECTORS_JS, "#email"))
        assert 1 <= len(result) <= 3

    def test_a_missing_element_suggests_nothing(self):
        result = run_page(lambda p: p.evaluate(SUGGEST_SELECTORS_JS, "#nope"))
        assert result == []

    def test_a_quote_in_an_aria_label_is_escaped(self):
        async def body(page):
            await page.set_content(
                '<button id="q" aria-label=\'Say "go"\'>x</button>'
            )
            return await page.evaluate(SUGGEST_SELECTORS_JS, "#q")
        result = run_page(body)
        assert any('\\"go\\"' in s for s in result), result
