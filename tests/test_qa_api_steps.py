"""Tests for API steps (Milestone 2) — UI + API assertions in one flow.

A scripted page implementing ``http_request`` exercises the api step and the
assert_status/json/latency/schema/header vocabulary without a network.
"""
from __future__ import annotations

import asyncio

from backend.modules.browser_agent import (
    BrowserAgentSession,
    _json_path,
)


def run(coro):
    return asyncio.run(coro)


class ApiPage:
    """Scripted page: navigation + a programmable HTTP responder."""

    def __init__(self, responses: dict | None = None):
        self.url = "about:blank"
        self.title = "API App"
        self.text = "hello"
        self.elements: list[dict] = []
        self.responses = responses or {}
        self.calls: list[dict] = []

    async def goto(self, url: str) -> None:
        self.url = url

    async def current_url(self) -> str:
        return self.url

    async def snapshot(self) -> dict:
        return {"url": self.url, "title": self.title,
                "elements": self.elements, "text": self.text}

    async def http_request(self, method, url, *, headers=None, body=None,
                           timeout_ms=15000):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": body})
        canned = self.responses.get(url)
        if canned is None:
            canned = next((v for k, v in self.responses.items()
                           if url.endswith(k)), None)
        if canned is None:
            return {"status": 404, "ok": False, "method": method, "url": url,
                    "latency_ms": 5, "json": None, "text": "",
                    "headers": {}}
        return {"method": method, "url": url,
                "status": canned.get("status", 200),
                "ok": canned.get("status", 200) < 400,
                "latency_ms": canned.get("latency_ms", 10),
                "json": canned.get("json"),
                "text": canned.get("text", ""),
                "headers": canned.get("headers", {})}


def make_session(page=None, **kwargs) -> BrowserAgentSession:
    page = page or ApiPage()
    defaults = dict(allow_domains=["example.com"], require_approval=False)
    defaults.update(kwargs)
    return BrowserAgentSession("bs-api", page, **defaults)


class TestJsonPath:
    def test_reads_nested_and_indexed(self):
        payload = {"data": {"items": [{"id": 7}]}, "ok": True}
        assert _json_path(payload, "data.items[0].id") == 7
        assert _json_path(payload, "ok") is True
        assert _json_path(payload, "") == payload

    def test_missing_paths_are_none(self):
        assert _json_path({"a": 1}, "a.b") is None
        assert _json_path({"a": [1]}, "a[3]") is None
        assert _json_path({"a": [1]}, "a[") is None


class TestApiStep:
    def test_get_passes_and_records_evidence(self):
        page = ApiPage({"/api/health": {"status": 200, "latency_ms": 12,
                                        "json": {"ok": True}}})
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "navigate", "url": "https://example.com/"},
            {"action": "api", "method": "GET",
             "url": "https://example.com/api/health",
             "expect_status": "2xx"},
            {"action": "assert_status", "value": "200"},
            {"action": "assert_json", "path": "ok", "expected": "True"},
            {"action": "assert_latency", "value": 50},
            {"action": "assert_schema", "required": ["ok"]},
        ]))
        assert report["ok"] is True
        api_step = report["steps"][1]
        assert api_step["api"]["status"] == 200
        assert page.calls[0]["method"] == "GET"

    def test_non_get_requires_approval(self):
        page = ApiPage({"/api/orders": {"status": 201,
                                       "json": {"id": 1}}})
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "api", "method": "POST",
             "url": "https://example.com/api/orders"},
        ]))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "approval_required"
        assert page.calls == []

    def test_post_with_approval_sends_body(self):
        page = ApiPage({"/api/orders": {"status": 201,
                                        "json": {"id": 42}}})
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "api", "method": "POST",
             "url": "https://example.com/api/orders",
             "json": {"sku": "ABC"}, "approve": True},
            {"action": "assert_status", "value": "2xx"},
            {"action": "assert_json", "path": "id", "expected": "42"},
        ]))
        assert report["ok"] is True
        assert page.calls[0]["body"] == {"sku": "ABC"}

    def test_status_mismatch_fails_the_step(self):
        page = ApiPage({"/api/boom": {"status": 500}})
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "api", "method": "GET",
             "url": "https://example.com/api/boom",
             "expect_status": "2xx"},
        ]))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "assertion_failed"

    def test_assert_without_api_step_is_an_error(self):
        session = make_session(ApiPage())
        report = run(session.run_flow([
            {"action": "assert_status", "value": "200"},
        ]))
        assert report["ok"] is False
        assert "no api step ran" in report["steps"][0]["error"]

    def test_unreachable_host_is_refused(self):
        session = make_session(ApiPage())
        report = run(session.run_flow([
            {"action": "api", "method": "GET",
             "url": "http://127.0.0.1:9999/admin"},
        ]))
        assert report["ok"] is False
        assert report["steps"][0]["reason"] == "unsafe_url"

    def test_schema_and_header_assertions(self):
        page = ApiPage({"/api/me": {
            "status": 200, "json": {"id": 1, "email": "a@b.c"},
            "headers": {"content-type": "application/json"}}})
        session = make_session(page)
        report = run(session.run_flow([
            {"action": "api", "method": "GET",
             "url": "https://example.com/api/me"},
            {"action": "assert_schema", "required": ["id", "email"]},
            {"action": "assert_header", "header": "content-type",
             "value": "json"},
        ]))
        assert report["ok"] is True

        bad = run(make_session(page).run_flow([
            {"action": "api", "method": "GET",
             "url": "https://example.com/api/me"},
            {"action": "assert_schema", "required": ["id", "phone"]},
        ]))
        assert bad["ok"] is False
        assert "phone" in bad["steps"][1]["error"]

        assert _json_path({"a": 1}, "a.b") is None
        assert _json_path({"a": [1]}, "a[3]") is None
        assert _json_path({"a": [1]}, "a[") is None
