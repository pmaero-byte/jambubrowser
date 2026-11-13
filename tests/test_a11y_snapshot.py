"""Regression: ``page.accessibility`` was removed from Playwright.

``collect_dom_snapshot`` called it unconditionally, so every audit logged a
failure and fell back to a DOM scrape. The snapshot now comes from
``locator.aria_snapshot()`` (or CDP), with the legacy API only as a last resort.
"""
from __future__ import annotations

import pytest

from backend.employees.base import AuditData
from backend.routes.audit_collect import (
    _format_cdp_ax_tree,
    accessibility_snapshot_text,
    collect_dom_snapshot,
)


class _Locator:
    def __init__(self, text=None, exc=None):
        self.text, self.exc = text, exc

    async def aria_snapshot(self):
        if self.exc:
            raise self.exc
        return self.text


class _CDP:
    def __init__(self, nodes):
        self.nodes = nodes

    async def send(self, method):
        assert method == "Accessibility.getFullAXTree"
        return {"nodes": self.nodes}

    async def detach(self):
        return None


class _Context:
    def __init__(self, nodes=None):
        self.nodes = nodes

    async def new_cdp_session(self, page):
        if self.nodes is None:
            raise RuntimeError("not chromium")
        return _CDP(self.nodes)


class _Page:
    """A current-Playwright page: no ``accessibility`` attribute at all."""

    def __init__(self, locator, nodes=None):
        self._locator = locator
        self.context = _Context(nodes)

    def locator(self, selector):
        assert selector == "body"
        return self._locator


async def test_uses_aria_snapshot_without_page_accessibility():
    page = _Page(_Locator('- heading "Oviyan Studio" [level=1]\n- button "Compile"'))
    assert not hasattr(page, "accessibility")
    text = await accessibility_snapshot_text(page)
    assert 'heading "Oviyan Studio"' in text
    data = AuditData(url="http://x")
    await collect_dom_snapshot(page, data)
    assert 'button "Compile"' in data.dom_snapshot


async def test_falls_back_to_cdp_tree():
    nodes = [
        {"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Doc"},
         "childIds": ["2", "3"]},
        {"nodeId": "2", "role": {"value": "generic"}, "childIds": ["4"]},
        {"nodeId": "3", "role": {"value": "button"}, "name": {"value": "Go"}},
        {"nodeId": "4", "role": {"value": "heading"}, "name": {"value": "Title"}},
    ]
    page = _Page(_Locator(exc=RuntimeError("old playwright")), nodes)
    text = await accessibility_snapshot_text(page)
    assert text.splitlines()[0] == 'RootWebArea "Doc"'
    assert '  heading "Title"' in text  # generic wrapper is flattened
    assert '  button "Go"' in text


def test_cdp_formatter_skips_ignored_nodes():
    nodes = [{"nodeId": "1", "role": {"value": "button"}, "ignored": True}]
    assert _format_cdp_ax_tree(nodes) == ""


async def test_real_chromium_aria_snapshot():
    """End-to-end against the installed Playwright + Chromium (skips if absent)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:  # pragma: no cover
        pytest.skip("playwright not installed")
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as e:  # pragma: no cover - no browser binary
            pytest.skip(f"chromium unavailable: {e}")
        try:
            page = await browser.new_page()
            await page.set_content("<h1>Hello</h1><button>Press</button>")
            text = await accessibility_snapshot_text(page)
        finally:
            await browser.close()
    assert 'heading "Hello"' in text
    assert 'button "Press"' in text
