"""Tests for the extracted assertion engine (backend/modules/browser_assertions.py).

These exist because the module is now a seam: a bug in an assertion should
be debuggable without reading the 3,000-line agent that calls it.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.browser_agent_errors import SessionRefused
from backend.modules.browser_assertions import (
    assert_selector,
    evaluate_assert,
    json_path,
)


def run(coro):
    return asyncio.run(coro)


class FakeSession:
    """Minimal session surface the assertion engine actually uses."""

    def __init__(self, *, visible=True, text="Submit", value="v", count=3,
                 checked=False, enabled=True, api=None, telemetry=None,
                 catalog=None):
        self.dom = {
            "#submit": {"visible": visible, "text": text, "value": value,
                        "count": count, "checked": checked, "enabled": enabled},
        }
        self._api = api
        self._telemetry = telemetry or {}
        self.catalog = catalog or {}
        self.calls: list[tuple[str, str]] = []
        # The agent stores the last API-step response here; the assertion
        # engine reads it (and refuses clearly when there is none).
        self._last_api = api

    async def _call_optional(self, name, *args, **kwargs):
        self.calls.append((name, args[0] if args else ""))
        if name == "is_visible_selector":
            return self.dom["#submit"]["visible"]
        if name == "text_of_selector":
            return self.dom["#submit"]["text"]
        if name == "value_of_selector":
            return self.dom["#submit"]["value"]
        if name == "count_selector":
            return self.dom["#submit"]["count"]
        if name == "is_checked_selector":
            return self.dom["#submit"]["checked"]
        if name == "is_enabled_selector":
            return self.dom["#submit"]["enabled"]
        if name == "made_request":
            return True
        raise SessionRefused("unsupported_action", f"no {name}")

    def _peek_telemetry(self):
        return self._telemetry

    def resolve_target(self, target):
        return target


class TestSelectorAssertions:
    def test_visible_and_hidden(self):
        s = FakeSession(visible=True)
        ok, detail = run(assert_selector(s, "visible", "#submit", ""))
        assert ok is True and "#submit" in detail
        ok, detail = run(assert_selector(s, "not_visible", "#submit", ""))
        assert ok is False and "still visible" in detail

    def test_text_contains_and_equals(self):
        s = FakeSession(text="Submit order")
        ok, detail = run(assert_selector(s, "text", "#submit", "order"))
        assert ok is True and "contains" in detail
        ok, _ = run(assert_selector(s, "text_equals", "#submit", "Submit order"))
        assert ok is True
        ok, detail = run(assert_selector(s, "text_equals", "#submit", "Nope"))
        assert ok is False and "!=" in detail

    def test_count_compares_against_expected_value(self):
        s = FakeSession(count=3)
        ok, detail = run(assert_selector(s, "count", "#submit", "3"))
        assert ok is True and "count ==" in detail

    def test_checked_and_enabled(self):
        s = FakeSession(checked=False, enabled=True)
        ok, _ = run(assert_selector(s, "unchecked", "#submit", ""))
        assert ok is True
        ok, _ = run(assert_selector(s, "checked", "#submit", ""))
        assert ok is False

    def test_unknown_selector_kind_is_refused(self):
        s = FakeSession()
        with pytest.raises(SessionRefused):
            run(assert_selector(s, "teleported", "#submit", ""))


class TestPageStateAssertions:
    def test_url_and_title(self):
        s = FakeSession()
        state = {"url": "https://example.com/done", "title": "Done"}
        ok, detail = run(evaluate_assert(s, {"action": "assert_url",
                                              "value": "done"}, state))
        assert ok is True and "done" in detail
        ok, _ = run(evaluate_assert(s, {"action": "assert_title", "value": "Nope"}, state))
        assert ok is False

    def test_api_status_assertion_reads_last_response(self):
        s = FakeSession(api={"status": 201, "latency_ms": 12,
                             "json": {"ok": True}, "headers": {"x-a": "b"}})
        ok, detail = run(evaluate_assert(s, {"action": "assert_status",
                                              "value": "201"}, {}))
        assert ok is True and "201" in detail

    def test_api_assertion_without_prior_request_explains_why(self):
        s = FakeSession(api=None)
        ok, detail = run(evaluate_assert(s, {"action": "assert_status",
                                              "value": "200"}, {}))
        assert ok is False and "api" in detail.lower()

    def test_console_clean_assertion(self):
        s = FakeSession(telemetry={"console_errors": [], "failed_requests": []})
        ok, _ = run(evaluate_assert(s, {"action": "assert_console_clean"}, {}))
        assert ok is True
        s2 = FakeSession(telemetry={"console_errors": [{"text": "boom"}]})
        ok, detail = run(evaluate_assert(s2, {"action": "assert_console_clean"}, {}))
        assert ok is False and "console" in detail.lower()


class TestJsonPath:
    def test_reads_nested_and_indexed(self):
        payload = {"data": {"items": [{"id": 7}]}, "ok": True}
        assert json_path(payload, "data.items[0].id") == 7
        assert json_path(payload, "ok") is True
        assert json_path(payload, "") == payload

    def test_missing_paths_return_none_not_raise(self):
        assert json_path({"a": 1}, "a.b") is None
        assert json_path({"a": [1]}, "a[9]") is None
        assert json_path({"a": 1}, "a[") is None
