"""Regression: ``/act`` and ``perform_actions_with_playwright`` swallowed failures.

A click on a missing selector was logged and skipped, and the request still
answered ``status: success``. Each action now has a recorded outcome and
failures surface as ``partial`` / ``error`` with ``failed_actions``.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.modules import playwright_scraper
from backend.modules.playwright_scraper import (
    ActionError,
    apply_action,
    run_actions,
    summarize_action_results,
)


class _Page:
    def __init__(self):
        self.calls = []

    async def click(self, selector, timeout=None):
        if selector == "#missing":
            raise TimeoutError("waiting for locator('#missing')")
        self.calls.append(("click", selector))

    async def fill(self, selector, value, timeout=None):
        self.calls.append(("fill", selector, value))

    async def evaluate(self, script, arg=None):
        self.calls.append(("evaluate", arg))
        return False if isinstance(arg, list) else None

    async def wait_for_timeout(self, ms):
        self.calls.append(("wait", ms))

    async def goto(self, url, **kwargs):
        self.calls.append(("goto", url))


def test_failures_are_recorded_not_skipped():
    page = _Page()
    results = asyncio.run(run_actions(page, [
        {"action": "click", "selector": "#ok"},
        {"action": "click", "selector": "#missing"},
        {"action": "teleport"},
        {"action": "type", "selector": "#q"},
        {"action": "wait", "value": "250"},
    ]))
    assert [r["ok"] for r in results] == [True, False, False, False, True]
    assert "TimeoutError" in results[1]["error"]
    assert "unsupported action 'teleport'" in results[2]["error"]
    summary = summarize_action_results(results)
    assert summary["status"] == "partial"
    assert summary["actions_failed"] == 3
    assert ("wait", 250) in page.calls


def test_click_xy_with_no_element_fails():
    with pytest.raises(ActionError):
        asyncio.run(apply_action(_Page(), {"action": "click_xy", "x": 50, "y": 50}))


def test_status_values():
    assert summarize_action_results([{"ok": True}])["status"] == "ok"
    assert summarize_action_results([{"ok": False}])["status"] == "failed"


def test_route_reports_partial(monkeypatch):
    from backend.routes import browser as browser_routes

    async def fake_perform(url, actions, **kwargs):
        results = [{"index": 0, "action": "click", "ok": True, "detail": "clicked #a"},
                   {"index": 1, "action": "click", "ok": False, "error": "TimeoutError: x"}]
        return {"success": False, "content": "page", "title": "t", "url": url,
                **summarize_action_results(results), "error": "1/2 action(s) failed"}

    class _Audit:
        def log(self, **kwargs):
            pass

    monkeypatch.setattr(playwright_scraper, "perform_actions_with_playwright", fake_perform)
    monkeypatch.setattr(browser_routes, "get_audit_logger", lambda: _Audit())
    req = browser_routes.MultiActionRequest(url="https://example.com/", steps=[
        {"action": "click", "selector": "#a"}, {"action": "click", "selector": "#b"}])
    body = asyncio.run(browser_routes.perform_actions(req))
    assert body["status"] == "partial"
    assert body["actions_verified"] is True
    assert body["failed_actions"][0]["index"] == 1
    assert "1/2" in body["message"]


async def test_real_chromium_missing_selector_is_reported():
    try:
        from playwright.async_api import async_playwright
    except ImportError:  # pragma: no cover
        pytest.skip("playwright not installed")
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as e:  # pragma: no cover
            pytest.skip(f"chromium unavailable: {e}")
        try:
            page = await browser.new_page()
            await page.set_content("<button id=go onclick=\"this.textContent='done'\">go</button>")
            results = await run_actions(page, [
                {"action": "click", "selector": "#go"},
                {"action": "click", "selector": "#nope"},
            ], timeout=500)
            text = await page.text_content("#go")
        finally:
            await browser.close()
    assert text == "done"
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
