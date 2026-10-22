"""Unit tests for the deterministic content checks (backend/modules/content_checks.py)."""

from __future__ import annotations

import pytest

from backend.modules import content_checks as cc


class TestJsonPath:
    PAYLOAD = {"a": {"b": [1, 2, {"c": "x"}]}, "n": None, "v": "0.4.0", "k": 3}

    def test_resolve_distinguishes_null_from_missing(self):
        assert cc.resolve_path(self.PAYLOAD, "n") is None
        assert cc.resolve_path(self.PAYLOAD, "zz") is cc._MISSING
        assert cc.resolve_path(self.PAYLOAD, "a.b[-1].c") == "x"
        assert cc.resolve_path(self.PAYLOAD, "a.b[9]") is cc._MISSING

    @pytest.mark.parametrize("path,op,expected,ok", [
        ("n", "exists", None, True),
        ("zz", "exists", None, False),
        ("zz", "not_exists", None, True),
        ("k", "equals", 3, True),
        ("k", "equals", "3", True),
        ("k", "equals", 4, False),
        ("v", "regex", r"^\d+\.\d+\.\d+$", True),
        ("v", "regex", r"^1\.", False),
        ("a.b", "contains", 2, True),
        ("a.b", "length", 3, True),
        ("k", "gte", 3, True),
        ("k", "lte", 2, False),
        ("a", "type", "object", True),
        ("v", "not_equals", "0.4.0", False),
    ])
    def test_ops(self, path, op, expected, ok):
        got, detail = cc.json_path_check(self.PAYLOAD, path, op, expected)
        assert got is ok, detail

    def test_unknown_op_is_refused(self):
        with pytest.raises(ValueError):
            cc.json_path_check(self.PAYLOAD, "k", "approximately", 3)


class TestSchema:
    SCHEMA = {
        "type": "object",
        "required": ["version", "formats"],
        "properties": {
            "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
            "formats": {"type": "array", "items": {"enum": ["svg", "html", "cbz", "pdf"]},
                        "minItems": 1},
            "count": {"type": "integer", "minimum": 0},
        },
    }

    def test_valid(self):
        assert cc.validate_schema({"version": "0.4.0", "formats": ["svg"]}, self.SCHEMA) == []

    def test_reports_every_violation_with_a_path(self):
        errors = cc.validate_schema({"version": "v4", "formats": ["gif"], "count": -1},
                                    self.SCHEMA)
        joined = " | ".join(errors)
        assert "$.version" in joined and "$.formats[0]" in joined and "$.count" in joined

    def test_missing_required(self):
        errors = cc.validate_schema({"formats": []}, self.SCHEMA)
        assert any("version" in e for e in errors)
        assert "$.formats: 0 items < 1" in errors

    def test_unknown_keyword_is_an_error_not_a_pass(self):
        assert cc.validate_schema(1, {"type": "integer", "multipleOf": 2})


GOOD_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<rect id="a" x="1" y="1" width="10" height="10"/>'
            '<text id="t" x="5" y="50">ஒரு சிறுமி</text></svg>')


class TestSvg:
    def test_good_svg_passes_everything(self):
        results = cc.svg_checks(GOOD_SVG, {
            "no_nan": True, "unique_ids": True, "no_script": True,
            "within_viewbox": True, "element_counts": {"rect": 1, "text": {"min": 1}},
            "text_contains": ["சிறுமி"], "script": {"name": "tamil", "min_ratio": 0.9},
        })
        assert all(r["ok"] for r in results), results

    def test_each_defect_is_caught(self):
        bad = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
               '<rect id="a" x="NaN" y="1" width="10" height="10"/>'
               '<rect id="a" x="500" y="1" width="10" height="10"/>'
               '<script>alert(1)</script><text onclick="x()">abc</text></svg>')
        results = {r["check"]: r for r in cc.svg_checks(bad, {
            "no_nan": True, "unique_ids": True, "no_script": True,
            "within_viewbox": True, "script": "tamil"})}
        for name in ("no_nan", "unique_ids", "no_script", "within_viewbox", "script"):
            assert results[name]["ok"] is False, (name, results[name])

    def test_malformed(self):
        results = cc.svg_checks("<svg><g></svg>", {})
        assert results[0]["check"] == "well_formed" and not results[0]["ok"]

    def test_unknown_check_refused(self):
        with pytest.raises(ValueError):
            cc.svg_checks(GOOD_SVG, {"pretty": True})


class TestTextFidelity:
    def test_dropped_vowel_sign_and_space(self):
        # What Oviyan's caption draws: U+0BC1 dropped twice, the space dropped.
        report = cc.text_fidelity("ஒரு சிறுமி", "ஒரசிறமி")
        assert not report["ok"]
        assert report["dropped_marks"].count("U+0BC1 TAMIL VOWEL SIGN U") == 2
        assert report["dropped_spaces"] == 1
        assert "U+0BC1" in cc.describe_fidelity(report)

    def test_foreign_script(self):
        report = cc.text_fidelity("யாதும் ஊரே!", "yaadhum oore!")
        assert report["foreign_letters"].get("latin", 0) > 0

    def test_nfc_equivalence(self):
        # Decomposed and precomposed forms of the same text are equal.
        assert cc.text_fidelity("é", "é")["ok"]


class TestCoverageAndHash:
    def test_prompt_coverage(self):
        report = cc.prompt_coverage("ஒரு சிறுமி புத்தகம்", ["ஒரு"])
        assert report["tokens"] == 3
        assert abs(report["coverage"] - 2 / 3) < 1e-9

    def test_prompt_coverage_counts_split_words_once(self):
        # Oviyan reports "படிக்கிறாள்" as two pieces split at the virama.
        report = cc.prompt_coverage("ஒரு சிறுமி புத்தகம் படிக்கிறாள்",
                                    ["ஒரு", "படிக", "கிறாள"])
        assert report["tokens"] == 4
        assert report["unmatched_words"] == ["ஒரு", "படிக்கிறாள்"]
        assert report["fragments"] == ["படிக", "கிறாள"]
        assert abs(report["coverage"] - 0.5) < 1e-9

    def test_stable_hash_ignores_volatile_keys(self):
        a = {"svg": "<svg/>", "render_ms": 3, "meta": {"render_ms": 1, "k": 1}}
        b = {"svg": "<svg/>", "render_ms": 9, "meta": {"render_ms": 7, "k": 1}}
        assert cc.stable_hash(a) != cc.stable_hash(b)
        assert cc.stable_hash(a, ignore=["render_ms"]) == cc.stable_hash(b, ignore=["render_ms"])
        assert cc.stable_hash(a, path="svg") == cc.stable_hash("<svg/>")
        assert cc.stable_hash(a).startswith("sha256:")


class TestElementCountSelectors:
    SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
           '<g data-expert="world.balloon"><path d="M0,0L1,1"/></g>'
           '<g data-expert="world.sky"/><path d="M1,1L2,2"/></svg>')

    def _counts(self, rule):
        return {r["check"]: r["ok"] for r in cc.svg_checks(self.SVG, {"element_counts": rule})}

    def test_attribute_and_tag_selectors(self):
        got = self._counts({'[data-expert="world.balloon"]': 1, "[data-expert]": 2,
                            'g[data-expert="world.sky"]': 1, "path": {"min": 2}})
        assert all(got.values()), got

    def test_bad_selector_is_rejected(self):
        with pytest.raises(ValueError):
            cc.svg_checks(self.SVG, {"element_counts": {"g > path": 1}})
