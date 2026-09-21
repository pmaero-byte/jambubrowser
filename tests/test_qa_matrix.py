"""Tests for QA case × dataset × viewport cross-product runs (Milestone 3)."""
from __future__ import annotations

import asyncio
import copy

import pytest

from backend.modules import qa_cases as qa


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "qa_matrix.db")
    monkeypatch.setenv("JAMBU_DB_PATH", db_path)
    from backend.core import database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    if db_mod._memory_db_conn is not None:
        try:
            db_mod._memory_db_conn.close()
        except Exception:
            pass
        db_mod._memory_db_conn = None
    db_mod.init_db(db_path)
    from backend.memory import reset_memory
    reset_memory()
    yield


STEPS = [{"action": "navigate", "url": "https://example.com/"},
         {"action": "assert_console_clean"}]
DESKTOP = {"name": "desktop", "viewport": {"width": 1280, "height": 800}}
MOBILE = {"name": "mobile", "viewport": {"width": 390, "height": 844}}


def pass_report():
    return {"ok": True, "passed": 2, "failed": 0, "total": 2,
            "duration_ms": 5,
            "steps": [{"i": 1, "action": "navigate", "status": "passed"},
                      {"i": 2, "action": "assert_console_clean",
                       "status": "passed"}],
            "console_errors": [], "artifacts": {}}


def fail_report():
    return {"ok": False, "passed": 1, "failed": 1, "total": 2,
            "duration_ms": 5,
            "steps": [{"i": 1, "action": "navigate", "status": "passed"},
                      {"i": 2, "action": "assert_console_clean",
                       "status": "failed", "reason": "assertion_failed",
                       "error": "console had errors"}],
            "console_errors": [], "artifacts": {}}


def script(monkeypatch, reports=None):
    """Serve one report per run_test call (last repeats); capture kwargs."""
    import backend.modules.browser_agent as ba
    calls: list[dict] = []
    reports = reports or [pass_report()]

    async def fake_run_test(self, **kwargs):
        calls.append(kwargs)
        idx = min(len(calls) - 1, len(reports) - 1)
        return copy.deepcopy(reports[idx])

    monkeypatch.setattr(ba.BrowserAgentService, "run_test", fake_run_test)
    return calls


class TestViewportMatrix:
    def test_variants_only_run_concurrently(self, monkeypatch):
        calls = script(monkeypatch)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        result = asyncio.run(qa.run_case(
            case["id"], viewport_matrix=[DESKTOP, MOBILE]))
        assert result["matrix"] is True
        assert result["cells"] == 2
        assert result["ok"] is True
        assert result["passed_cells"] == 2
        variants = {v["variant"] for v in result["by_variant"]}
        assert variants == {"desktop", "mobile"}
        runs = qa.list_runs(case["id"])
        assert sorted(r["variant"] for r in runs) == ["desktop", "mobile"]
        # context options must reach the browser context
        for call in calls:
            assert call["context_options"]["viewport"] in (
                {"width": 1280, "height": 800},
                {"width": 390, "height": 844})

    def test_cross_product_rows_x_variants(self, monkeypatch):
        calls = script(monkeypatch)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        rows = [{"user": "alice"}, {"user": "bob"}]
        result = asyncio.run(qa.run_case(
            case["id"], dataset_rows=rows,
            viewport_matrix=[DESKTOP, MOBILE]))
        assert result["cells"] == 4
        assert result["rows"] == 2
        assert result["variants"] == 2
        assert result["passed_cells"] == 4
        runs = qa.list_runs(case["id"])
        combos = {(r["dataset_index"], r["variant"]) for r in runs}
        assert combos == {(0, "desktop"), (0, "mobile"),
                          (1, "desktop"), (1, "mobile")}
        desktop = next(v for v in result["by_variant"]
                       if v["variant"] == "desktop")
        assert desktop["cells"] == 2 and desktop["passed"] == 2

    def test_failed_cell_fails_gate(self, monkeypatch):
        # mobile variant fails, desktop passes
        import backend.modules.browser_agent as ba

        async def fake_run_test(self, **kwargs):
            vp = (kwargs.get("context_options") or {}).get("viewport")
            report = fail_report() if vp == MOBILE["viewport"] \
                else pass_report()
            return copy.deepcopy(report)

        monkeypatch.setattr(ba.BrowserAgentService, "run_test", fake_run_test)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        result = asyncio.run(qa.run_case(
            case["id"], viewport_matrix=[DESKTOP, MOBILE]))
        assert result["ok"] is False
        assert result["passed_cells"] == 1
        assert result["failed_cells"] == 1
        mobile = next(v for v in result["by_variant"]
                      if v["variant"] == "mobile")
        assert mobile["failed"] == 1
        assert qa.get_case(case["id"])["last_status"] == "failed"

    def test_junit_labels_include_variant(self, monkeypatch):
        script(monkeypatch)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        result = asyncio.run(qa.run_case(
            case["id"], junit=True, viewport_matrix=[MOBILE]))
        assert "[mobile]" in result["junit"]

    def test_duplicate_dataset_rows_get_distinct_cells(self, monkeypatch):
        calls = script(monkeypatch)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        rows = [{"user": "same"}, {"user": "same"}]
        result = asyncio.run(qa.run_case(
            case["id"], dataset_rows=rows,
            viewport_matrix=[{"name": "v1",
                              "viewport": {"width": 800, "height": 600}}]))
        assert result["cells"] == 2
        indexes = sorted(r["dataset_index"] for r in qa.list_runs(case["id"]))
        assert indexes == [0, 1]
        assert len(calls) == 2

    def test_matrix_shape_validation(self, monkeypatch):
        script(monkeypatch)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        with pytest.raises(ValueError):
            asyncio.run(qa.run_case(case["id"],
                                    viewport_matrix="desktop"))  # type: ignore
        with pytest.raises(ValueError):
            asyncio.run(qa.run_case(case["id"],
                                    viewport_matrix=["desktop"]))


class TestOverview:
    def test_overview_aggregates_stats(self, monkeypatch):
        script(monkeypatch, [pass_report(), fail_report()])
        c1 = qa.create_case("alpha", "https://example.com/", STEPS)
        c2 = qa.create_case("beta", "https://example.com/", STEPS)
        asyncio.run(qa.run_case(c1["id"]))
        asyncio.run(qa.run_case(c2["id"]))

        from backend.routes import qa as qa_routes
        feed = qa_routes.overview()
        assert feed["count"] == 2
        assert feed["summary"]["cases"] == 2
        assert feed["summary"]["pass_rate"] == 0.5
        by_name = {e["name"]: e for e in feed["cases"]}
        assert by_name["alpha"]["stats"]["pass_rate"] == 1.0
        assert by_name["beta"]["stats"]["pass_rate"] == 0.0
        assert by_name["beta"]["stats"]["flake_rate"] == 0.0

    def test_overview_counts_quarantine_lane(self, monkeypatch):
        script(monkeypatch)
        case = qa.create_case("parked", "https://example.com/", STEPS)
        qa.quarantine_case(case["id"], reason="flaky history")
        from backend.routes import qa as qa_routes
        feed = qa_routes.overview()
        assert feed["summary"]["quarantined"] == 1
        entry = next(e for e in feed["cases"] if e["id"] == case["id"])
        assert entry["stats"]["quarantined"] is True

    def test_overview_empty(self):
        from backend.routes import qa as qa_routes
        feed = qa_routes.overview()
        assert feed["count"] == 0
        assert feed["summary"]["pass_rate"] is None
