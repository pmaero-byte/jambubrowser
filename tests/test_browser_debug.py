"""Unit tests for the pure browser-debugging helpers."""
from __future__ import annotations

import asyncio

from backend.modules.browser_agent import PlaywrightPage
from backend.modules.browser_debug import (
    NetworkPolicy,
    SourceMapData,
    compile_network,
    decode_vlq,
    diff_elements,
    map_url_for,
    match_network,
    parse_stack_frames,
    serialize_body,
)


class TestNetwork:
    def test_compile_and_match_mock(self):
        rules = compile_network({"mocks": [{"url": "**/api/user", "json": {"a": 1}}]})
        hit = match_network(rules, "http://x/api/user", "GET")
        assert hit is not None and hit.action == "fulfill"
        assert hit.status == 200
        assert serialize_body(hit.body, hit.content_type) == '{"a": 1}'

    def test_substring_pattern_matches(self):
        rules = compile_network({"fail": ["/analytics/"]})
        assert match_network(rules, "https://x.com/analytics/collect") is not None
        assert match_network(rules, "https://x.com/api") is None

    def test_method_filter(self):
        rules = compile_network({"mocks": [{"url": "/api", "method": "POST", "json": {}}]})
        assert match_network(rules, "https://x/api", "GET") is None
        assert match_network(rules, "https://x/api", "POST") is not None

    def test_precedence_is_author_order(self):
        rules = compile_network({
            "mocks": [{"url": "/api", "json": {"mocked": True}}],
            "fail": ["/api"],
        })
        hit = match_network(rules, "https://x/api", "GET")
        assert hit.action == "fulfill"  # mock listed before fail wins

    def test_delay_rule(self):
        rules = compile_network({"delay": [{"url": "/slow", "ms": 250}]})
        hit = match_network(rules, "https://x/slow")
        assert hit.action == "delay" and hit.ms == 250

    def test_empty_network(self):
        assert compile_network(None) == []


class TestRequestPolicy:
    def test_blocks_non_http_protocols_and_disallowed_hosts(self):
        policy = NetworkPolicy(["example.com"], resolver=lambda host: ["93.184.216.34"])
        assert policy.decide("ftp://example.com/file").reason == "blocked_protocol"
        assert policy.decide("file:///etc/passwd").reason == "blocked_protocol"
        assert policy.decide("https://evil.test/x").reason == "blocked_domain"

    def test_subresources_fetch_xhr_and_images_use_same_policy(self):
        policy = NetworkPolicy(["example.com"], resolver=lambda host: ["93.184.216.34"])
        for kind in ("image", "script", "font", "fetch", "xhr"):
            decision = policy.decide("https://cdn.evil.test/a", kind=kind)
            assert decision.allowed is False
            assert decision.reason == "blocked_domain"

    def test_private_literal_and_dns_rebinding_are_blocked(self):
        policy = NetworkPolicy(["example.com", "127.0.0.1"], resolver=lambda host: ["127.0.0.1"])
        assert policy.decide("http://127.0.0.1/").reason == "private_address"
        assert policy.decide("https://example.com/").reason == "private_address"

    def test_websocket_protocol_and_redirect_destination_are_checked(self):
        policy = NetworkPolicy(["example.com"], resolver=lambda host: ["93.184.216.34"])
        assert policy.decide("wss://example.com/socket", kind="websocket").allowed is True
        assert policy.decide("https://evil.test/after-redirect").reason == "blocked_domain"

    def test_allow_private_is_explicit_and_report_is_bounded(self):
        policy = NetworkPolicy(["localhost", "127.0.0.1"], allow_private=True, max_events=2)
        assert policy.decide("http://127.0.0.1:3000/").allowed is True
        for i in range(4):
            policy.decide(f"https://evil.test/{i}")
        report = policy.report()
        assert len(report["requests_seen"]) == 2
        assert len(report["blocked_requests"]) == 2
        assert report["allow_private"] is True
        assert report["websocket_supported"] is None


class _Request:
    def __init__(self, url, method="GET", resource_type="document"):
        self.url, self.method, self.resource_type = url, method, resource_type


class _Route:
    def __init__(self, request):
        self.request = request
        self.action = None
        self.error_code = None

    async def abort(self, error_code=None):
        self.action = "abort"
        self.error_code = error_code

    async def continue_(self):
        self.action = "continue"

    async def fulfill(self, **kwargs):
        self.action = "fulfill"


class _WebSocketRoute:
    def __init__(self, url):
        self.url = url
        self.action = None
        self.close_code = None

    async def close(self, code=None, reason=None):
        self.action, self.close_code = "close", code

    async def connect_to_server(self):
        self.action = "connect"


class _PlaywrightPage:
    def __init__(self):
        self.context = self
        self.http_handler = None
        self.websocket_handler = None

    def on(self, *_args):
        pass

    async def route(self, _pattern, handler):
        self.http_handler = handler

    async def route_web_socket(self, _pattern, handler):
        self.websocket_handler = handler



class _Response:
    def __init__(self, status, headers=None, text=""):
        self.status = status
        self.ok = status < 400
        self.headers = headers or {}
        self._text = text

    async def text(self):
        return self._text


class _RequestContext:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class TestRequestRouting:
    def test_route_aborts_disallowed_subresource_and_allows_allowed_request(self):
        async def check():
            raw = _PlaywrightPage()
            policy = NetworkPolicy(["example.com"], resolver=lambda host: ["93.184.216.34"])
            page = PlaywrightPage(raw, network_policy=policy)
            await page.setup_network({})
            assert policy.report()["enforced"] is True
            blocked = _Route(_Request("https://evil.test/pixel.png", resource_type="image"))
            allowed = _Route(_Request("https://example.com/app.js", resource_type="script"))
            await raw.http_handler(blocked)
            await raw.http_handler(allowed)
            assert (blocked.action, blocked.error_code) == ("abort", "blockedbyclient")
            assert allowed.action == "continue"
        asyncio.run(check())

    def test_api_context_disables_redirects_and_blocks_destination(self):
        async def check():
            response = _Response(302, {"location": "https://evil.test/next"})
            request_ctx = _RequestContext(response)
            raw = _PlaywrightPage()
            raw.context = type("Context", (), {"request": request_ctx})()
            policy = NetworkPolicy(["example.com"], resolver=lambda host: ["93.184.216.34"])
            page = PlaywrightPage(raw, network_policy=policy)
            result = await page.http_request("GET", "https://example.com/start")
            assert result["blocked"] is True
            assert result["reason"] == "blocked_domain"
            assert request_ctx.calls[0][1]["max_redirects"] == 0
        asyncio.run(check())

    def test_websocket_route_closes_disallowed_destination(self):
        async def check():
            raw = _PlaywrightPage()
            policy = NetworkPolicy(["example.com"], resolver=lambda host: ["93.184.216.34"])
            page = PlaywrightPage(raw, network_policy=policy)
            await page.setup_network({})
            blocked = _WebSocketRoute("wss://evil.test/socket")
            allowed = _WebSocketRoute("wss://example.com/socket")
            await raw.websocket_handler(blocked)
            await raw.websocket_handler(allowed)
            assert (blocked.action, blocked.close_code) == ("close", 1008)
            assert allowed.action == "connect"
        asyncio.run(check())


class TestDomDiff:
    def test_added_removed_changed(self):
        before = [
            {"ref": "@e1", "name": "A", "value": ""},
            {"ref": "@e2", "name": "B", "value": ""},
        ]
        after = [
            {"ref": "@e1", "name": "A", "value": "typed"},
            {"ref": "@e3", "name": "C", "value": ""},
        ]
        delta = diff_elements(before, after)
        assert delta["added"] == 1 and delta["added_names"] == ["C"]
        assert delta["removed"] == 1 and delta["removed_names"] == ["B"]
        assert delta["changed"] == 1 and delta["changed_names"] == ["A"]

    def test_no_change(self):
        same = [{"ref": "@e1", "name": "A", "value": ""}]
        delta = diff_elements(same, same)
        assert delta == {"added": 0, "removed": 0, "changed": 0,
                         "added_names": [], "removed_names": [], "changed_names": []}


class TestSourceMaps:
    def test_decode_vlq(self):
        assert decode_vlq("AAAA") == [0, 0, 0, 0]
        assert decode_vlq("AACA") == [0, 0, 1, 0]
        # 'D' -> index 3 -> continuation bit unset, value 3 -> signed -1
        assert decode_vlq("D") == [-1]

    def test_lookup_maps_to_original_source(self):
        sm = SourceMapData({
            "version": 3,
            "sources": ["src/App.tsx"],
            "mappings": "AAAA;AACA",
        })
        first = sm.lookup(1, 0)
        assert first["source"] == "src/App.tsx" and first["line"] == 1
        second = sm.lookup(2, 0)
        assert second["line"] == 2

    def test_lookup_out_of_range(self):
        sm = SourceMapData({"version": 3, "sources": ["a.ts"], "mappings": "AAAA"})
        assert sm.lookup(99, 0) is None

    def test_parse_stack_frames(self):
        stack = "Error: x\n    at foo (http://localhost:3000/src/App.tsx:42:10)"
        frames = parse_stack_frames(stack)
        assert frames[0]["url"] == "http://localhost:3000/src/App.tsx"
        assert frames[0]["line"] == 42 and frames[0]["column"] == 10

    def test_map_url_for(self):
        assert map_url_for("https://x/app.js") == "https://x/app.js.map"
        assert map_url_for("https://x/app.js?v=1") == "https://x/app.js.map"
