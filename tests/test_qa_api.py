"""QA cases API — Milestone 1 managed test-case layer."""
from __future__ import annotations

import pytest

from backend.modules import qa_cases as qa


@pytest.fixture
def authed_client(test_client, monkeypatch, tmp_path):
    from backend.core import database as db_mod

    db_path = str(tmp_path / "qa_api.db")
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    db_mod.init_db(db_path)
    return test_client


STEPS = [
    {"action": "navigate", "url": "https://example.com/"},
    {"action": "assert_console_clean"},
]


class TestQaApi:
    def test_full_loop(self, authed_client, monkeypatch):
        import backend.modules.browser_agent as ba

        async def fake_run_test(self, **kwargs):
            return {"ok": True, "passed": 2, "failed": 0, "total": 2,
                    "duration_ms": 5, "steps": [
                        {"i": 1, "action": "navigate",
                         "status": "passed"},
                        {"i": 2, "action": "assert_console_clean",
                         "status": "passed"},
                    ], "console_errors": [], "artifacts": {}}

        monkeypatch.setattr(ba.BrowserAgentService, "run_test",
                            fake_run_test)

        c = authed_client
        created = c.post("/qa/cases", json={
            "name": "example smoke", "url": "https://example.com/",
            "steps": STEPS, "goal": "smoke test", "kind": "smoke",
        })
        assert created.status_code == 200, created.text
        case_id = created.json()["id"]

        listed = c.get("/qa/cases")
        assert listed.json()["count"] == 1

        run = c.post(f"/qa/cases/{case_id}/run", json={})
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "passed"

        runs = c.get(f"/qa/cases/{case_id}/runs")
        assert runs.json()["count"] == 1

        detail = c.get(f"/qa/cases/{case_id}")
        assert detail.json()["stats"]["pass_rate"] == 1.0

    def test_from_goal(self, authed_client):
        c = authed_client
        res = c.post("/qa/from-goal", json={
            "name": "example login", "url": "https://example.com/login",
            "goal": "test login",
        })
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["kind"] == "login"
        assert len(body["steps"]) >= 3
        assert body["plan"]["source"] == "template"

    def test_local_url_needs_flag(self, authed_client):
        c = authed_client
        res = c.post("/qa/cases", json={
            "name": "local", "url": "http://localhost:3000/",
            "steps": STEPS,
        })
        assert res.status_code == 422
        res = c.post("/qa/cases", json={
            "name": "local", "url": "http://localhost:3000/",
            "steps": STEPS, "local": True,
        })
        assert res.status_code == 200, res.text
