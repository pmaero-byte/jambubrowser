"""Plan library: normalisation, matching, outcome scoring, eviction."""

import json

import pytest

from backend.agent import plan_library


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("JAMBU_PLAN_LIBRARY_PATH", str(tmp_path / "plans.json"))
    plan_library.reset_plan_library()
    yield plan_library.get_plan_library()
    plan_library.reset_plan_library()


def _plan(action="navigate"):
    return {"steps": [{"index": 0, "description": "open the page", "tool": action}]}


def test_normalize_collapses_format():
    assert plan_library.normalize_goal("  Audit https://example.com/ A11y!! ") == "audit https example com a11y"


def test_put_then_exact_match(lib):
    lib.put("Audit https://acme.com for accessibility", _plan())
    hit = lib.match("audit acme com for accessibility")
    assert hit is not None
    assert hit["success_rate"] == 1.0


def test_fuzzy_match_near_duplicate_goal(lib):
    lib.put("Audit acme store for accessibility issues", _plan())
    hit = lib.match("Audit the acme store for accessibility issues")
    assert hit is not None


def test_no_match_for_unrelated_goal(lib):
    lib.put("Audit acme store for accessibility issues", _plan())
    assert lib.match("Generate a logo for my startup") is None


def test_outcome_updates_success_rate(lib):
    lib.put("Audit acme", _plan())
    lib.record_outcome("Audit acme", success=True)
    lib.record_outcome("Audit acme", success=False)
    entry = lib.top(1)[0]
    assert entry["uses"] == 3
    assert entry["successes"] == 2
    assert entry["success_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_persistence_roundtrip(lib, tmp_path):
    lib.put("Audit acme", _plan())
    path = tmp_path / "plans.json"
    assert path.exists()
    reloaded = plan_library.PlanLibrary(str(path))
    assert reloaded.match("audit acme") is not None


def test_clear_and_remove(lib):
    lib.put("Audit acme", _plan())
    lib.put("Scrape the blog", _plan())
    assert lib.remove("audit acme") is True
    assert len(lib) == 1
    assert lib.clear() == 1
    assert len(lib) == 0


def test_corrupt_file_degrades_to_empty(tmp_path, monkeypatch):
    path = tmp_path / "plans.json"
    path.write_text("{ not json")
    monkeypatch.setenv("JAMBU_PLAN_LIBRARY_PATH", str(path))
    plan_library.reset_plan_library()
    lib = plan_library.get_plan_library()
    assert len(lib) == 0
    plan_library.reset_plan_library()


def test_advise_planner_renders_template(lib):
    lib.put("Audit acme", _plan())
    advice = plan_library.advise_planner("audit acme")
    assert "similar goal succeeded" in advice
    assert "[navigate]" in advice
    assert plan_library.advise_planner("something else entirely") == ""
