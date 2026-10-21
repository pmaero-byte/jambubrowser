"""Regression: ``{"action": "wait", "ms": N}`` ignored N.

The step fell through to the network-quiet wait, so a 1500 ms pause lasted
however long the page took to go idle. ``ms`` is now an honoured fixed pause.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.modules import browser_agent
from backend.modules.browser_agent import MAX_WAIT_MS, SessionRefused, _wait_duration_ms
from backend.modules.browser_codegen import flow_to_playwright, playwright_to_flow
from tests.test_browser_flow import FlowPage, make_session, seed


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *args, **kwargs):
        recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(browser_agent.asyncio, "sleep", fake_sleep)
    return recorded


def _run(steps):
    page = FlowPage()
    seed(page)
    session = make_session(page)
    return page, asyncio.run(session.run_flow(
        [{"action": "navigate", "url": "https://example.com/"}] + steps))


def test_fixed_pause_is_honoured(sleeps):
    _, report = _run([{"action": "wait", "ms": 1500}])
    assert report["ok"] is True
    assert 1.5 in sleeps
    assert "1500ms" in str(report["steps"][-1])


def test_alias_duration_ms(sleeps):
    _, report = _run([{"action": "wait", "duration_ms": 250}])
    assert report["ok"] is True
    assert 0.25 in sleeps


def test_ms_after_condition_is_extra_settle_time(sleeps):
    _, report = _run([{"action": "wait", "text": "Welcome", "ms": 400}])
    assert report["ok"] is True
    assert 0.4 in sleeps


def test_duration_is_capped():
    assert _wait_duration_ms({"ms": 10 ** 9}) == MAX_WAIT_MS
    assert _wait_duration_ms({"ms": "0"}) == 0
    assert _wait_duration_ms({}) is None


@pytest.mark.parametrize("bad", [-5, "soon"])
def test_invalid_duration_is_refused(bad):
    with pytest.raises(SessionRefused) as exc:
        _wait_duration_ms({"ms": bad})
    assert exc.value.reason == "invalid_step"


def test_invalid_duration_fails_the_flow(sleeps):
    _, report = _run([{"action": "wait", "ms": -1}])
    assert report["ok"] is False


def test_codegen_round_trip():
    ts = flow_to_playwright([{"action": "wait", "ms": 1200}])
    assert "page.waitForTimeout(1200)" in ts
    flow = playwright_to_flow(ts)
    assert {"action": "wait", "ms": 1200} in flow["steps"]
