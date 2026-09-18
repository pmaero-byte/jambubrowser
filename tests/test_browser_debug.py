"""Unit tests for the pure browser-debugging helpers."""
from __future__ import annotations

from backend.modules.browser_debug import (
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
