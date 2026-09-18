"""Tests for flow monitors — recurring agent test flows."""
from __future__ import annotations

import asyncio

import pytest

from backend.modules import flow_monitor as fm


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "flow_monitors.db")
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
    yield


def run(coro):
    return asyncio.run(coro)


STEPS = [{"action": "navigate", "url": "http://localhost:3000/"},
         {"action": "assert_console_clean"}]


class TestCrud:
    def test_create_get_list_delete(self):
        created = fm.create_monitor("smoke", "http://localhost:3000", STEPS,
                                    interval_minutes=30)
        assert created["id"] and created["name"] == "smoke"
        assert created["steps"] == STEPS
        assert created["enabled"] is True

        assert len(fm.list_monitors()) == 1
        updated = fm.update_monitor(created["id"], enabled=False, name="renamed")
        assert updated["enabled"] is False and updated["name"] == "renamed"

        assert fm.delete_monitor(created["id"]) is True
        assert fm.get_monitor(created["id"]) is None

    def test_interval_is_floored(self):
        created = fm.create_monitor("m", "http://x", STEPS, interval_minutes=0)
        assert created["interval_minutes"] == fm.MIN_INTERVAL_MINUTES


class TestRunMonitor:
    def _patch_service(self, monkeypatch, report):
        import backend.modules.browser_agent as ba

        class FakeService:
            async def run_test(self, **kwargs):
                return report

        monkeypatch.setattr(ba, "BrowserAgentService", FakeService)

    def test_successful_run_persists(self, monkeypatch):
        self._patch_service(monkeypatch, {
            "ok": True, "passed": 2, "failed": 0, "total": 2,
            "duration_ms": 42, "steps": [], "console_errors": [],
        })
        monitor = fm.create_monitor("m", "http://localhost:3000", STEPS)
        result = run(fm.run_monitor(monitor["id"]))
        assert result["status"] == "passed" and result["ok"] is True

        runs = fm.list_runs(monitor["id"])
        assert len(runs) == 1 and runs[0]["passed"] == 2
        assert fm.get_monitor(monitor["id"])["last_status"] == "passed"

    def test_failed_run_alerts(self, monkeypatch):
        self._patch_service(monkeypatch, {
            "ok": False, "passed": 1, "failed": 1, "total": 2,
            "duration_ms": 42, "console_errors": ["boom"],
            "steps": [{"i": 2, "action": "click", "status": "failed",
                       "reason": "target_not_found", "error": "no element"}],
        })
        sent = {}

        async def fake_webhook(url, payload):
            sent["url"] = url
            sent["payload"] = payload
            return True

        monkeypatch.setattr(fm, "send_webhook", fake_webhook)
        monitor = fm.create_monitor("m", "http://localhost:3000", STEPS,
                                    webhook_url="https://hooks.example.com/x")
        result = run(fm.run_monitor(monitor["id"]))
        assert result["status"] == "failed"
        assert result["failed_steps"][0]["reason"] == "target_not_found"
        assert sent["payload"]["event"] == "flow.regression"
        assert sent["url"] == "https://hooks.example.com/x"

    def test_error_is_recorded(self, monkeypatch):
        import backend.modules.browser_agent as ba

        class BoomService:
            async def run_test(self, **kwargs):
                raise RuntimeError("browser crashed")

        monkeypatch.setattr(ba, "BrowserAgentService", BoomService)
        monkeypatch.setattr(fm, "send_webhook", lambda *a, **k: _noop())
        monitor = fm.create_monitor("m", "http://localhost:3000", STEPS)
        result = run(fm.run_monitor(monitor["id"]))
        assert result["status"] == "error"
        assert "browser crashed" in result["error"]


async def _noop():
    return True


class TestScheduler:
    def test_due_when_never_run(self):
        from backend.modules.audit_monitor import is_due
        monitor = {"enabled": True, "last_run_at": None, "interval_minutes": 60}
        assert is_due(monitor) is True


class TestRoutes:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from backend.engine import app
        with TestClient(app) as c:
            yield c

    def test_crud_route_flow(self, client, monkeypatch):
        import backend.modules.browser_agent as ba

        class FakeService:
            async def run_test(self, **kwargs):
                return {"ok": True, "passed": 2, "failed": 0, "total": 2,
                        "duration_ms": 5, "steps": [], "console_errors": []}

        monkeypatch.setattr(ba, "BrowserAgentService", FakeService)

        created = client.post("/browser/monitors", json={
            "name": "smoke", "url": "http://localhost:3000", "steps": STEPS,
        })
        assert created.status_code == 200, created.text
        monitor_id = created.json()["id"]

        assert client.get("/browser/monitors").json()["count"] == 1
        run_resp = client.post(f"/browser/monitors/{monitor_id}/run")
        assert run_resp.status_code == 200 and run_resp.json()["ok"] is True
        assert len(client.get(f"/browser/monitors/{monitor_id}/runs").json()["runs"]) == 1

        assert client.delete(f"/browser/monitors/{monitor_id}").status_code == 200
        assert client.get(f"/browser/monitors/{monitor_id}").status_code == 404

    def test_create_requires_steps(self, client):
        resp = client.post("/browser/monitors", json={
            "name": "bad", "url": "http://x", "steps": [],
        })
        assert resp.status_code == 422
