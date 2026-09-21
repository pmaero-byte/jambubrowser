"""Tests for flake handling, quarantine and SARIF (Milestone 2)."""
from __future__ import annotations

import asyncio
import copy

import pytest

from backend.modules import qa_cases as qa
from backend.modules import qa_datasets as qd


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "qa_flake.db")
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
            "console_errors": ["boom"], "artifacts": {}}


def script(monkeypatch, reports):
    """Serve one report per run_test call (last one repeats)."""
    import backend.modules.browser_agent as ba
    calls = {"n": 0}

    async def fake_run_test(self, **kwargs):
        idx = min(calls["n"], len(reports) - 1)
        calls["n"] += 1
        return copy.deepcopy(reports[idx])

    monkeypatch.setattr(ba.BrowserAgentService, "run_test", fake_run_test)
    return calls


class TestFlakeRetry:
    def test_retry_flip_is_flaky_but_green(self, monkeypatch):
        script(monkeypatch, [fail_report(), pass_report()])
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        result = asyncio.run(qa.run_case(case["id"]))
        assert result["ok"] is True
        assert result["status"] == "flaky"
        assert result["attempts"] == 2
        runs = qa.list_runs(case["id"])
        assert len(runs) == 2
        assert runs[0]["flaky"] is True and runs[0]["attempt"] == 2
        assert runs[1]["flaky"] is False and runs[1]["attempt"] == 1
        assert qa.get_case(case["id"])["last_status"] == "flaky"
        stats = qa.case_stats(case["id"])
        assert stats["flake_rate"] == 0.5 and stats["pass_rate"] == 0.5

    def test_auto_retry_disabled_single_attempt(self, monkeypatch):
        calls = script(monkeypatch, [fail_report(), pass_report()])
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        qa.set_auto_retry(case["id"], False)
        result = asyncio.run(qa.run_case(case["id"]))
        assert result["ok"] is False and result["status"] == "failed"
        assert result["attempts"] == 1 and calls["n"] == 1

    def test_double_failure_stays_failed(self, monkeypatch):
        script(monkeypatch, [fail_report(), fail_report()])
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        result = asyncio.run(qa.run_case(case["id"]))
        assert result["ok"] is False and result["status"] == "failed"
        assert result["attempts"] == 2


class TestQuarantine:
    def _make_flaky(self, monkeypatch, times: int) -> dict:
        # A repeating fail→pass cycle: every run flakes (retry flips it).
        script(monkeypatch, [fail_report(), pass_report()] * times)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        for _ in range(times):
            asyncio.run(qa.run_case(case["id"]))
        return qa.get_case(case["id"])

    def test_auto_quarantine_after_threshold(self, monkeypatch):
        monkeypatch.setattr(qa, "FLAKY_QUARANTINE_AFTER", 3)
        case = self._make_flaky(monkeypatch, 3)
        assert case["quarantined"] is True
        assert "flakes" in (case["quarantine_reason"] or "")

    def test_quarantined_case_is_skipped(self, monkeypatch):
        script(monkeypatch, [pass_report()])
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        qa.quarantine_case(case["id"], reason="known flake")
        with pytest.raises(ValueError, match="quarantined"):
            asyncio.run(qa.run_case(case["id"]))
        forced = asyncio.run(qa.run_case(case["id"], force=True))
        assert forced["ok"] is True

    def test_auto_promote_after_green_streak(self, monkeypatch):
        script(monkeypatch, [pass_report()])
        monkeypatch.setattr(qa, "PROMOTE_AFTER_GREEN_STREAK", 2)
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        qa.quarantine_case(case["id"], reason="stale")
        for _ in range(2):
            asyncio.run(qa.run_case(case["id"], force=True))
        promoted = qa.get_case(case["id"])
        assert promoted["quarantined"] is False
        assert promoted["quarantine_reason"] is None

    def test_manual_unquarantine(self):
        case = qa.create_case("smoke", "https://example.com/", STEPS)
        assert qa.quarantine_case(case["id"])["quarantined"] is True
        assert qa.unquarantine_case(case["id"])["quarantined"] is False
        assert qa.quarantine_case(9999) is None


class TestSarif:
    def test_renders_results_with_stable_rules(self):
        sarif = qd.runs_to_sarif("login smoke", [
            {"ok": False, "duration_ms": 100, "error": None,
             "failed_steps": [{"i": 2, "action": "click",
                               "reason": "target_not_found",
                               "error": "no element matches 'Sign in'"}],
             "console_errors": [], "attempt": 1, "flaky": False},
        ], case_severity="high", url="https://example.com/login")
        assert sarif["version"] == "2.1.0"
        run = sarif["runs"][0]
        assert run["tool"]["driver"]["name"] == "jambubrowser-qa"
        result = run["results"][0]
        assert result["ruleId"] == "QA001"
        assert result["level"] == "error"          # high → error
        assert "target_not_found" in result["message"]["text"]
        assert result["partialFingerprints"]["jambubrowserCaseStep"]

    def test_passing_runs_produce_no_results(self):
        sarif = qd.runs_to_sarif("smoke", [
            {"ok": True, "duration_ms": 5, "failed_steps": [],
             "console_errors": [], "error": None, "flaky": False},
        ])
        assert sarif["runs"][0]["results"] == []

    def test_flaky_run_is_a_note(self):
        sarif = qd.runs_to_sarif("smoke", [
            {"ok": True, "duration_ms": 5, "failed_steps": [],
             "console_errors": [], "error": None, "flaky": True,
             "attempt": 2},
        ])
        result = sarif["runs"][0]["results"][0]
        assert result["ruleId"] == "QA010" and result["level"] == "note"
        assert "flaky" in result["message"]["text"]

    def test_runner_error_becomes_result(self):
        sarif = qd.runs_to_sarif("smoke", [
            {"ok": False, "duration_ms": 5, "failed_steps": [],
             "console_errors": [], "error": "browser executable missing"},
        ])
        assert sarif["runs"][0]["results"][0]["ruleId"] == "QA009"

    def test_severity_maps_to_level(self):
        for severity, level in (("critical", "error"), ("medium", "warning"),
                                ("low", "note")):
            sarif = qd.runs_to_sarif("c", [
                {"ok": False, "failed_steps": [
                    {"i": 1, "action": "x", "reason": "assertion_failed",
                     "error": "nope"}], "console_errors": [],
                 "duration_ms": 1, "error": None},
            ], case_severity=severity)
            assert sarif["runs"][0]["results"][0]["level"] == level
