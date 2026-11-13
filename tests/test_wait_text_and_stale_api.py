"""Two flow-engine bugs found while running the Oviyan pack.

1. ``wait {"text": ...}`` called ``page.wait_for_function(script, [text], ...)``;
   Playwright takes ``arg`` by keyword only, so every real text wait raised
   TypeError before waiting.
2. An ``api`` step that raised left the previous response in place, so the
   next ``assert_status`` passed on the *earlier* call's 200.
"""
from __future__ import annotations

import asyncio
import inspect

from backend.modules.browser_agent import BrowserAgentSession
from backend.modules.browser_page import PlaywrightPage


class StrictPage:
    """Mimics Playwright's real signature: ``arg`` is keyword-only."""

    def __init__(self):
        self.calls = []

    def on(self, *_a, **_k):
        return None

    async def wait_for_function(self, expression, *, arg=None, timeout=None, polling=None):
        self.calls.append((expression, arg, timeout))
        return True


def test_wait_for_visible_text_passes_arg_by_keyword():
    page = StrictPage()
    adapter = PlaywrightPage(page)
    asyncio.run(adapter.wait_for_visible_text("Compiled", timeout_ms=1234))
    assert page.calls and page.calls[0][1] == ["Compiled"] and page.calls[0][2] == 1234


def test_real_playwright_signature_is_keyword_only():
    try:
        from playwright.async_api import Page
    except ImportError:  # pragma: no cover
        return
    param = inspect.signature(Page.wait_for_function).parameters["arg"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


class FlakyApiPage:
    def __init__(self):
        self.url = "about:blank"

    async def goto(self, url):
        self.url = url

    async def current_url(self):
        return self.url

    async def snapshot(self):
        return {"url": self.url, "title": "t", "elements": [], "text": ""}

    async def http_request(self, method, url, *, headers=None, body=None, timeout_ms=15000):
        if method == "HEAD":
            raise RuntimeError("Parse Error: Data after `Connection: close`")
        return {"method": method, "url": url, "status": 200, "ok": True, "latency_ms": 1,
                "json": None, "text": "ok", "body": "ok", "headers": {}}


def test_failed_api_step_does_not_leave_the_previous_response():
    session = BrowserAgentSession("bs-stale", FlakyApiPage(), allow_domains=["example.com"],
                                  require_approval=False)
    report = asyncio.run(session.run_flow([
        {"action": "navigate", "url": "https://example.com/"},
        {"action": "api", "url": "https://example.com/"},
        {"action": "assert_status", "value": "200"},
        {"action": "api", "method": "HEAD", "url": "https://example.com/"},
        {"action": "assert_status", "value": "200"},
    ]))
    statuses = [s["status"] for s in report["steps"]]
    assert statuses[:3] == ["passed", "passed", "passed"]
    assert statuses[3] in ("failed", "inconclusive")  # the request itself errored
    assert statuses[4] == "failed", report["steps"][4]
    assert "no api step" in report["steps"][4]["error"]
