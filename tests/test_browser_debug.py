"""Unit tests for the pure browser-debugging helpers."""
from __future__ import annotations

import asyncio

from backend.modules.browser_agent import PlaywrightPage
from backend.modules.browser_debug import (
    DEFAULT_OBSERVE_FIELDS,
    MAX_OBSERVE_ROWS,
    NetworkPolicy,
    SourceMapData,
    compact_observation,
    compile_network,
    decode_vlq,
    diff_elements,
    diff_view,
    element_identity,
    map_url_for,
    match_network,
    parse_stack_frames,
    search_text,
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


class _ServerWebSocketRoute:
    """Server-side half, as Playwright actually hands it back.

    ``connect_to_server()`` is synchronous and returns *this*, and once it is
    called both directions must be forwarded by hand -- the earlier fake here
    modelled it as a coroutine returning nothing, which is what let the
    ``await`` bug through unnoticed.
    """

    def __init__(self):
        self.sent: list = []

    def on_message(self, handler):
        self.server_message_handler = handler

    def on_close(self, handler):
        pass

    def send(self, message):
        self.sent.append(message)

    def close(self, code=None, reason=None):
        pass


class _WebSocketRoute:
    """Page-side route, matching Playwright 1.63's signatures."""

    def __init__(self, url):
        self.url = url
        self.action = None
        self.close_code = None
        self.sent: list = []
        self.server = _ServerWebSocketRoute()

    async def close(self, code=None, reason=None):
        self.action, self.close_code = "close", code

    def connect_to_server(self):
        self.action = "connect"
        return self.server

    def on_message(self, handler):
        self.page_message_handler = handler

    def on_close(self, handler):
        self.page_close_handler = handler

    def send(self, message):
        self.sent.append(message)


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
            # An allowed socket is proxied in both directions, not merely
            # connected -- a one-way proxy silently drops every server frame.
            allowed.server.server_message_handler("down")
            allowed.page_message_handler("up")
            assert allowed.sent == ["down"]
            assert allowed.server.sent == ["up"]
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


def _state(**overrides) -> dict:
    """A small page: one button, one visible input, one hidden input."""
    state = {
        "url": "https://example.com/form",
        "title": "Form",
        "text": "Order confirmed\nThanks for your purchase\nFooter text",
        "elements": [
            {"ref": "@e1", "tag": "button", "role": "button",
             "name": "Submit order", "value": "", "visible": True, "disabled": False},
            {"ref": "@e2", "tag": "input", "role": "input", "name": "Email",
             "value": "a@b.com", "visible": True, "disabled": False},
            {"ref": "@e3", "tag": "input", "role": "input", "name": "Secret",
             "value": "", "visible": False, "disabled": False},
        ],
        "count": 3,
    }
    state.update(overrides)
    return state


class TestCompactObservation:
    def test_projects_to_positional_rows_and_counts_the_hidden(self):
        view = compact_observation(_state())
        assert view["columns"] == list(DEFAULT_OBSERVE_FIELDS)
        assert view["total"] == 3
        assert view["hidden_omitted"] == 1        # the hidden input is dropped
        assert view["matched"] == 2
        assert view["shown"] == 2
        assert view["url"] == "https://example.com/form"
        assert [row[0] for row in view["rows"]] == ["@e1", "@e2"]
        # Never the raw catalog: this is the whole point of the projection.
        assert "elements" not in view

    def test_include_hidden_opts_back_in(self):
        view = compact_observation(_state(), include_hidden=True)
        assert view["hidden_omitted"] == 0
        assert view["matched"] == 3

    def test_ref_is_always_kept_but_unknown_fields_are_dropped(self):
        view = compact_observation(_state(), fields=["name", "bogus"])
        # 'ref' is prepended because the agent needs a handle to act on;
        # 'bogus' is not a real field and must not reach the output.
        assert view["columns"] == ["ref", "name"]

    def test_query_narrows_the_catalog(self):
        view = compact_observation(_state(), query="email")
        assert view["matched"] == 1
        assert "Email" in view["rows"][0]

    def test_roles_filter_by_role_or_tag(self):
        view = compact_observation(_state(), roles=["input"])
        assert view["matched"] == 1               # hidden input excluded
        assert "Email" in view["rows"][0]
        assert compact_observation(_state(), roles=["button"])["matched"] == 1

    def test_match_any_is_a_candidate_set(self):
        both = compact_observation(_state(), query="email submit")
        assert both["matched"] == 0               # 'all' semantics: needs both
        either = compact_observation(_state(), query="email submit", match="any")
        assert either["matched"] == 2

    def test_text_search_returns_only_matching_lines(self):
        view = compact_observation(_state(), text="order confirmed")
        assert view["text"]["hit"] is True
        assert any("Order confirmed" in line for line in view["text"]["matches"])

    def test_text_miss_is_reported_rather_than_omitted(self):
        """A search that ran and found nothing must say so.

        Dropping the key would be indistinguishable from "no search
        requested", and the model would retry the same query forever.
        """
        view = compact_observation(_state(), text="nonexistent phrase")
        assert view["text"]["hit"] is False
        assert view["text"]["matches"] == []

    def test_max_tokens_shrinks_rows_and_says_so(self):
        many = _state(elements=[
            {"ref": f"@e{i}", "tag": "button", "role": "button",
             "name": f"Button number {i}", "value": "", "visible": True}
            for i in range(1, 41)
        ], count=40)
        view = compact_observation(many, max_tokens=60)
        assert view["total"] == 40
        assert view["shown"] < 40                # rows were dropped
        assert view["truncated"] is True
        assert view["omitted"]                    # and the drop is disclosed
        assert view["hint"]                       # with a way to widen it
        assert view["tokens_estimate"] > 0

    def test_budget_is_not_reported_as_truncated_when_it_fits(self):
        view = compact_observation(_state(), max_tokens=10_000)
        assert view["truncated"] is False
        assert "omitted" not in view

    def test_row_cap_bounds_a_large_catalog(self):
        many = _state(elements=[
            {"ref": f"@e{i}", "tag": "button", "role": "button",
             "name": f"B{i}", "value": "", "visible": True}
            for i in range(1, 121)
        ], count=120)
        view = compact_observation(many)
        assert len(view["rows"]) == MAX_OBSERVE_ROWS
        assert view["matched"] == 120             # the full count is still honest


class TestElementIdentity:
    def test_ignores_query_string_and_trailing_slash(self):
        """Refs renumber on every read, so identity must key on content.

        Otherwise one inserted node renumbers every later ref and the delta
        reports the whole page as churn.
        """
        a = {"tag": "a", "role": "link", "name": "Next", "href": "https://x.com/next/"}
        b = {"tag": "a", "role": "link", "name": "Next", "href": "https://x.com/next?utm=1"}
        assert element_identity(a) == element_identity(b)

    def test_different_content_is_a_different_identity(self):
        a = {"tag": "a", "role": "link", "name": "Next", "href": "/next"}
        b = {"tag": "a", "role": "link", "name": "Back", "href": "/next"}
        assert element_identity(a) != element_identity(b)


class TestDiffView:
    def test_added_changed_removed_with_before_and_after(self):
        before = [{"ref": "@e1", "tag": "button", "role": "button",
                   "name": "Save", "value": ""}]
        after = [
            {"ref": "@e9", "tag": "button", "role": "button",
             "name": "Save", "value": "typed"},
            {"ref": "@e5", "tag": "a", "role": "link", "name": "Done", "href": "/done"},
        ]
        delta = diff_view(before, after, fields=["ref", "role", "name"])
        assert delta["counts"] == {"added": 1, "changed": 1, "removed": 0}
        assert delta["changed"][0]["before"][0] == "@e1"
        assert delta["changed"][0]["after"][0] == "@e9"
        assert delta["added"][0][0] == "@e5"
        assert delta["columns"] == ["ref", "role", "name"]

    def test_compact_observation_delta_mode_reuses_it(self):
        before = _state()["elements"]
        after = [
            before[0],
            {**before[1], "value": "typed@example.com"},
            {"ref": "@e4", "tag": "a", "role": "link", "name": "Done", "href": "/done"},
        ]
        view = compact_observation(_state(elements=after), changed_since=before)
        assert "rows" not in view               # delta mode, not a full listing
        assert view["delta"]["counts"]["added"] == 1
        assert view["delta"]["counts"]["changed"] == 1
        assert view["shown"] == 2               # added + changed, not the page


class TestSearchText:
    def test_returns_matching_lines_with_context(self):
        found = search_text("Header\nOrder confirmed\nFooter", "order confirmed")
        assert found["hit"] is True
        assert found["total_hits"] == 1
        assert any("Order confirmed" in line for line in found["matches"])

    def test_miss_is_reported_not_silent(self):
        assert search_text("Header", "nothing here") == {"hit": False, "matches": []}

    def test_empty_inputs_short_circuit(self):
        assert search_text("", "x") == {}
        assert search_text("some text", "") == {}

    def test_repeated_lines_are_deduplicated(self):
        found = search_text("\n".join(["Buy now"] * 50), "buy now")
        assert found["hit"] is True
        assert len(found["matches"]) == 1        # one line, not fifty
