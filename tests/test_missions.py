"""Tests: mission create → list round-trip, scheduler start/stop, execution.

Regression coverage for three bugs found during use-case validation:
1. ``POST /mission`` wrote straight to the DB while ``GET /mission/list``
   read the scheduler's in-memory store → created missions were invisible.
2. ``POST /mission/start-scheduler`` called a ``MissionScheduler.start()``
   method that didn't exist → HTTP 500.
3. No research handler was ever registered, so every due mission failed
   with status 'error' even when the loop ran.
"""
from __future__ import annotations

import asyncio

import pytest


def asyncio_run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_scheduler():
    """Keep scheduler state from leaking between tests."""
    from backend.modules import missions as missions_mod
    missions_mod._scheduler = None
    yield
    if missions_mod._scheduler is not None:
        missions_mod._scheduler.stop()
    missions_mod._scheduler = None


class TestMissionRoundTrip:
    def test_create_then_list_shows_the_mission(self, client):
        created = client.post("/mission", json={"query": "validate round-trip"})
        assert created.status_code == 200, created.text
        mid = created.json()["mission_id"]

        listed = client.get("/mission/list")
        assert listed.status_code == 200
        missions = listed.json()["missions"]
        assert any(m["id"] == mid for m in missions), missions
        row = next(m for m in missions if m["id"] == mid)
        assert row["query"] == "validate round-trip"
        assert row["status"] == "active"

    def test_stop_updates_status_in_list(self, client):
        mid = client.post(
            "/mission", json={"query": "stop me"},
        ).json()["mission_id"]

        stopped = client.post("/mission/stop", json={"mission_id": mid})
        assert stopped.status_code == 200

        missions = client.get("/mission/list").json()["missions"]
        row = next(m for m in missions if m["id"] == mid)
        assert row["status"] == "stopped"

    def test_stop_unknown_mission_still_200(self, client):
        resp = client.post("/mission/stop", json={"mission_id": "nope"})
        assert resp.status_code == 200

    def test_list_is_served_from_db_across_processes(self, client):
        """A mission created in a previous process (DB row, empty store)
        must still be listed."""
        from backend.core.database import get_db_cursor
        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO missions (id, query, status, last_run, next_run, schedule) "
                "VALUES ('legacy1', 'from a past session', 'active', 0, 0, 'none')",
            )
        missions = client.get("/mission/list").json()["missions"]
        assert any(m["id"] == "legacy1" for m in missions)


class TestSchedulerLifecycle:
    def test_start_scheduler_route_returns_started(self, client):
        resp = client.post("/mission/start-scheduler")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] in ("started", "already_running")
        # Idempotent: a second call doesn't start a second loop.
        resp2 = client.post("/mission/start-scheduler")
        assert resp2.status_code == 200

    def test_start_loads_missions_from_db(self, client):
        from backend.core.database import get_db_cursor
        from backend.modules.missions import get_scheduler

        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO missions (id, query, status, last_run, next_run, schedule) "
                "VALUES ('db1', 'loaded from db', 'active', 0, 0, 'none')",
            )
        scheduler = get_scheduler()
        assert scheduler.get_mission("db1") is None
        started = asyncio_run(scheduler.start())
        assert started is True
        assert scheduler.get_mission("db1") is not None
        assert asyncio_run(scheduler.start()) is False  # already running
        scheduler.stop()

    def test_due_mission_executes_and_persists_result(self, client):
        from backend.modules.missions import get_scheduler

        scheduler = get_scheduler()
        scheduler.set_research_handler(
            lambda q: _async_return(f"answer for {q}")
        )
        mission = asyncio_run(scheduler.add_mission(query="due now"))
        asyncio_run(scheduler._execute_mission(mission))

        # Recurring missions stay 'active'; a run is visible via
        # run_count/last_run plus the persisted result row.
        assert mission.run_count == 1
        assert mission.last_run > 0
        results = scheduler.get_results(mission.id)
        assert results and results[0]["success"] is True
        assert "answer for due now" in results[0]["result_text"]

    def test_run_state_survives_reload(self, client):
        """last_run must be written back to the DB: a reload used to
        resurrect the mission as due, re-executing it every tick."""
        from backend.modules.missions import get_scheduler

        scheduler = get_scheduler()
        scheduler.set_research_handler(lambda q: _async_return("ok"))
        mission = asyncio_run(scheduler.add_mission(query="reload me"))
        asyncio_run(scheduler._execute_mission(mission))

        asyncio_run(scheduler.load_from_db())
        reloaded = scheduler.get_mission(mission.id)
        assert reloaded.last_run > 0
        assert reloaded.should_run_now() is False

    def test_mission_without_handler_errors(self):
        # Standalone scheduler (the app's singleton gets a handler in the
        # engine lifespan, so this path needs an isolated instance).
        from backend.modules.missions import MissionScheduler

        scheduler = MissionScheduler()
        mission = asyncio_run(scheduler.add_mission(query="no handler"))
        asyncio_run(scheduler._execute_mission(mission))
        assert mission.status == "error"


async def _async_return(value):
    return value
