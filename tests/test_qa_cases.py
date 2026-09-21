"""Tests for Milestone 1 QA cases — managed test-case model + heal loop."""
from __future__ import annotations

import pytest

from backend.modules import qa_cases as qa


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "qa_cases.db")
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


STEPS = [
    {"action": "navigate", "url": "http://localhost:3000/"},
    {"action": "click", "target": "Sign in"},
    {"action": "assert_console_clean"},
]


class TestCrud:
    def test_create_get_list_delete(self):
        created = qa.create_case("login smoke", "http://localhost:3000",
                                 STEPS, goal="test login", kind="login",
                                 severity="high", owner="qa-lead")
        assert created["id"] and created["kind"] == "login"
        assert created["steps"] == STEPS
        assert created["procedural_key"] == "qa:login:localhost"
        assert created["enabled"] is True

        assert len(qa.list_cases()) == 1
        updated = qa.update_case(created["id"], severity="critical",
                                 enabled=False)
        assert updated["severity"] == "critical"
        assert updated["enabled"] is False

        assert qa.delete_case(created["id"]) is True
        assert qa.get_case(created["id"]) is None

    def test_validation(self):
        with pytest.raises(ValueError):
            qa.create_case("", "http://localhost:3000", STEPS)
        with pytest.raises(ValueError):
            qa.create_case("x", "http://localhost:3000", [])
        with pytest.raises(ValueError):
            qa.create_case("x", "", STEPS)

    def test_unknown_kind_falls_back_to_smoke(self):
        created = qa.create_case("x", "http://localhost:3000", STEPS,
                                 kind="teleport")
        assert created["kind"] == "smoke"


class TestRunVerdict:
    def _patch(self, monkeypatch, report):
        import backend.modules.browser_agent as ba

        async def fake_run_test(self, **kwargs):
            return dict(report)

        monkeypatch.setattr(ba.BrowserAgentService, "run_test",
                            fake_run_test)
        import backend.modules.qa_cases as qam
        monkeypatch.setattr(qam, "_run_one_shot",
                            lambda *a, **k: {"ok": True})

    def test_pass_persists_run_and_stats(self, monkeypatch):
        self._patch(monkeypatch, {
            "ok": True, "passed": 3, "failed": 0, "total": 3,
            "duration_ms": 10, "steps": [
                {"i": 1, "action": "navigate", "status": "passed"},
                {"i": 2, "action": "click", "status": "passed"},
                {"i": 3, "action": "assert_console_clean",
                 "status": "passed"},
            ], "console_errors": [], "artifacts": {},
        })
        import asyncio
        case = qa.create_case("smoke", "http://localhost:3000", STEPS)
        result = asyncio.run(qa.run_case(case["id"], actor="t"))
        assert result["status"] == "passed" and result["ok"] is True
        assert result["healed_steps"] == 0

        runs = qa.list_runs(case["id"])
        assert len(runs) == 1 and runs[0]["passed"] == 3
        stats = qa.case_stats(case["id"])
        assert stats["pass_rate"] == 1.0 and stats["runs"] == 1
        assert qa.get_case(case["id"])["last_status"] == "passed"

    def test_fail_without_candidates_stays_failed(self, monkeypatch):
        self._patch(monkeypatch, {
            "ok": False, "passed": 2, "failed": 1, "total": 3,
            "duration_ms": 10, "steps": [
                {"i": 1, "action": "navigate", "status": "passed"},
                {"i": 2, "action": "click", "status": "passed"},
                {"i": 3, "action": "assert_console_clean",
                 "status": "failed", "reason": "console_errors",
                 "error": "boom"},
            ], "console_errors": ["boom"], "artifacts": {},
        })
        import asyncio
        case = qa.create_case("smoke", "http://localhost:3000", STEPS)
        result = asyncio.run(qa.run_case(case["id"]))
        assert result["status"] == "failed"
        assert result["healed_steps"] == 0
        assert qa.list_heal_events(case["id"]) == []

class TestHealLoop:
    def _patch_flow(self, monkeypatch, reports):
        """reports consumed per run_test call (main run, then probes)."""
        import asyncio
        import copy

        import backend.modules.browser_agent as ba
        import backend.modules.qa_cases as qam
        calls = {"n": 0}

        async def fake_run_test(self, **kwargs):
            idx = min(calls["n"], len(reports) - 1)
            calls["n"] += 1
            return copy.deepcopy(reports[idx])

        monkeypatch.setattr(ba.BrowserAgentService, "run_test",
                            fake_run_test)

        def fake_one_shot(url, probe, **k):
            idx = min(calls["n"], len(reports) - 1)
            calls["n"] += 1
            return copy.deepcopy(reports[idx])

        monkeypatch.setattr(qam, "_run_one_shot", fake_one_shot)

    def test_heal_proposes_but_does_not_rewrite(self, monkeypatch):
        main_report = {
            "ok": False, "passed": 2, "failed": 1, "total": 3,
            "duration_ms": 10, "steps": [
                {"i": 1, "action": "navigate", "status": "passed"},
                {"i": 2, "action": "click", "status": "failed",
                 "reason": "target_not_found",
                 "error": "no element matches 'Sign in'",
                 "candidates": [{"ref": "@e9", "name": "Log in"}]},
                {"i": 3, "action": "assert_console_clean",
                 "status": "passed"},
            ], "console_errors": [], "artifacts": {},
        }
        probe_ok = {"ok": True, "passed": 2, "failed": 0, "total": 2,
                    "duration_ms": 5, "steps": [], "console_errors": [],
                    "artifacts": {}}
        self._patch_flow(monkeypatch, [main_report, probe_ok])
        import asyncio
        case = qa.create_case("login", "http://localhost:3000", STEPS,
                              kind="login")
        result = asyncio.run(qa.run_case(case["id"]))
        assert result["status"] == "passed"  # healed step counts
        assert result["healed_steps"] == 1

        heals = qa.list_heal_events(case["id"])
        assert len(heals) == 1
        heal = heals[0]
        assert heal["status"] == "proposed"
        assert heal["old_target"] == "Sign in"
        assert heal["new_target"] == "Log in"
        # Stored case untouched until a lead accepts.
        assert qa.get_case(case["id"])["steps"][1]["target"] == "Sign in"

        decided = qa.decide_heal_event(heal["id"], accept=True,
                                       actor="lead")
        assert decided["status"] == "accepted"
        assert qa.get_case(case["id"])["steps"][1]["target"] == "Log in"

    def test_reject_leaves_case_untouched(self, monkeypatch):
        main_report = {
            "ok": False, "passed": 1, "failed": 1, "total": 2,
            "duration_ms": 10, "steps": [
                {"i": 1, "action": "navigate", "status": "passed"},
                {"i": 2, "action": "click", "status": "failed",
                 "reason": "target_ambiguous",
                 "error": "matched 2 elements",
                 "candidates": [{"ref": "@e3", "name": "Submit form"}]},
            ], "console_errors": [], "artifacts": {},
        }
        probe_ok = {"ok": True, "passed": 2, "failed": 0, "total": 2,
                    "duration_ms": 5, "steps": [], "console_errors": [],
                    "artifacts": {}}
        self._patch_flow(monkeypatch, [main_report, probe_ok])
        import asyncio
        case = qa.create_case("c", "http://localhost:3000", STEPS[:2])
        result = asyncio.run(qa.run_case(case["id"]))
        assert result["healed_steps"] == 1
        heal = qa.list_heal_events(case["id"])[0]
        decided = qa.decide_heal_event(heal["id"], accept=False)
        assert decided["status"] == "rejected"
        assert qa.get_case(case["id"])["steps"][1]["target"] == "Sign in"

    def test_decide_idempotent(self):
        case = qa.create_case("c", "http://localhost:3000", STEPS[:2])
        heal = qa.propose_heal_event(case["id"], None, step_index=1,
                                     old_target="A", new_target="B")
        first = qa.decide_heal_event(heal["id"], accept=True)
        second = qa.decide_heal_event(heal["id"], accept=False)
        assert first["status"] == "accepted"
        assert second["status"] == "accepted"  # no flip after disposal

