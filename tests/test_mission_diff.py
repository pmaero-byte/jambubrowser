"""Tests: backend/core/mission_diff.py + /mission/results/compare endpoint."""
import json
import pytest


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setenv("JAMBU_DB_PATH", ":memory:")
    monkeypatch.setenv("JAMBU_LLM_PROVIDER", "mock")


class TestTokenize:
    def test_empty_returns_empty_set(self):
        from backend.core.mission_diff import tokenize
        assert tokenize("") == set()
        assert tokenize(None) == set()

    def test_lowercases(self):
        from backend.core.mission_diff import tokenize
        assert tokenize("Hello World") == {"hello", "world"}

    def test_strips_punctuation(self):
        from backend.core.mission_diff import tokenize
        assert tokenize("hello, world!") == {"hello", "world"}


class TestDiffSources:
    def test_added_removed_kept(self):
        from backend.core.mission_diff import diff_sources
        d = diff_sources(
            ["https://a.com", "https://b.com"],
            ["https://b.com", "https://c.com"],
        )
        assert d["added"] == ["https://c.com"]
        assert d["removed"] == ["https://a.com"]
        assert d["kept"] == ["https://b.com"]

    def test_empty_inputs(self):
        from backend.core.mission_diff import diff_sources
        d = diff_sources([], [])
        assert d == {"added": [], "removed": [], "kept": []}

    def test_none_inputs(self):
        from backend.core.mission_diff import diff_sources
        d = diff_sources(None, None)
        assert d == {"added": [], "removed": [], "kept": []}

    def test_filters_falsy(self):
        from backend.core.mission_diff import diff_sources
        d = diff_sources(["a", "", None], ["a", "b"])
        assert "a" in d["kept"]
        assert "b" in d["added"]


class TestDiffText:
    def test_identical_text(self):
        from backend.core.mission_diff import diff_text
        d = diff_text("hello world", "hello world")
        assert d["changed"] is False
        assert d["length_delta"] == 0
        assert d["similarity"] == 1.0

    def test_completely_different(self):
        from backend.core.mission_diff import diff_text
        d = diff_text("apple banana", "cherry durian")
        assert d["changed"] is True
        assert d["similarity"] == 0.0
        assert "cherry" in d["words_added"]
        assert "apple" in d["words_removed"]

    def test_length_delta(self):
        from backend.core.mission_diff import diff_text
        d = diff_text("hi", "hello there")
        assert d["length_delta"] == len("hello there") - len("hi")

    def test_empty_text_similarity(self):
        from backend.core.mission_diff import diff_text
        d = diff_text("", "")
        assert d["similarity"] == 1.0
        assert d["changed"] is False

    def test_words_added_capped_at_20(self):
        from backend.core.mission_diff import diff_text
        a = "a"
        b = " ".join(f"word{i}" for i in range(50))
        d = diff_text(a, b)
        assert len(d["words_added"]) == 20


class TestDiffStatus:
    def test_unchanged(self):
        from backend.core.mission_diff import diff_status
        assert diff_status(True, True)["changed"] is False

    def test_changed(self):
        from backend.core.mission_diff import diff_status
        assert diff_status(True, False)["changed"] is True

    def test_none_passes_through(self):
        from backend.core.mission_diff import diff_status
        d = diff_status(None, True)
        assert d["success_a"] is None
        assert d["success_b"] is True
        assert d["changed"] is True


class TestCompareResults:
    def test_combines_all_three_axes(self):
        from backend.core.mission_diff import compare_results
        diff = compare_results(
            {"id": 1, "run_at": 100.0, "result_text": "the cat sat", "success": True},
            {"id": 2, "run_at": 200.0, "result_text": "the dog ran", "success": False},
            sources_a=["https://a.com"],
            sources_b=["https://b.com"],
        )
        assert diff["result_a"]["id"] == 1
        assert diff["result_b"]["id"] == 2
        assert "cat" in diff["text"]["words_removed"]
        assert "dog" in diff["text"]["words_added"]
        assert diff["status"]["changed"] is True
        assert diff["sources"]["added"] == ["https://b.com"]
        assert diff["sources"]["removed"] == ["https://a.com"]


class TestCompareEndpoint:
    def _insert_result(self, mission_id, text, success, sources_json=None):
        from backend.core.database import get_db_cursor
        import time
        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO missions (id, query, status, created_at) VALUES (?, ?, 'active', ?)",
                (mission_id, f"query for {mission_id}", time.time()),
            )
            cursor.execute(
                "INSERT INTO mission_results (mission_id, run_at, result_text, success, sources_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (mission_id, time.time(), text, 1 if success else 0, sources_json),
            )
            return cursor.lastrowid

    def test_compare_two_results(self):
        from backend.routes.missions import compare_mission_results
        a_id = self._insert_result("mdiff-m1", "the quick brown fox", True)
        b_id = self._insert_result("mdiff-m1", "the quick red fox", True)
        import asyncio
        result = asyncio.run(compare_mission_results(result_a=a_id, result_b=b_id))
        assert result["result_a"]["id"] == a_id
        assert result["result_b"]["id"] == b_id
        assert "brown" in result["text"]["words_removed"]
        assert "red" in result["text"]["words_added"]

    def test_compare_with_sources(self):
        from backend.routes.missions import compare_mission_results
        a_id = self._insert_result("mdiff-m2", "x", True, sources_json=json.dumps(["https://a.com", "https://b.com"]))
        b_id = self._insert_result("mdiff-m2", "y", True, sources_json=json.dumps(["https://b.com", "https://c.com"]))
        import asyncio
        result = asyncio.run(compare_mission_results(result_a=a_id, result_b=b_id))
        assert result["sources"]["added"] == ["https://c.com"]
        assert result["sources"]["removed"] == ["https://a.com"]
        assert result["sources"]["kept"] == ["https://b.com"]

    def test_rejects_same_id(self):
        from backend.routes.missions import compare_mission_results
        from fastapi import HTTPException
        rid = self._insert_result("mdiff-m3", "x", True)
        import asyncio
        with pytest.raises(HTTPException) as exc:
            asyncio.run(compare_mission_results(result_a=rid, result_b=rid))
        assert exc.value.status_code == 400

    def test_404_when_result_missing(self):
        from backend.routes.missions import compare_mission_results
        from fastapi import HTTPException
        a_id = self._insert_result("mdiff-m4", "x", True)
        import asyncio
        with pytest.raises(HTTPException) as exc:
            asyncio.run(compare_mission_results(result_a=a_id, result_b=99999))
        assert exc.value.status_code == 404

    def test_handles_invalid_sources_json(self):
        from backend.routes.missions import compare_mission_results
        a_id = self._insert_result("mdiff-m5", "x", True, sources_json="not-valid-json{")
        b_id = self._insert_result("mdiff-m5", "y", True)
        import asyncio
        result = asyncio.run(compare_mission_results(result_a=a_id, result_b=b_id))
        assert result["sources"]["added"] == []
        assert result["sources"]["removed"] == []
