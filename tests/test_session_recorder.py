"""Tests for backend.modules.session_recorder — record/replay of scripted runs."""
from __future__ import annotations

import pytest

from backend.core.database import get_db_cursor, init_db
from backend.modules import session_recorder as sr


@pytest.fixture(autouse=True)
def _init_db(tmp_path, monkeypatch):
    """Point the recorder at a fresh temp DB for every test."""
    db_path = str(tmp_path / "rec_test.db")
    init_db(db_path)
    monkeypatch.setattr(sr, "get_db_cursor", lambda: get_db_cursor(db_path))
    yield


@pytest.fixture(autouse=True)
def _fake_playwright(monkeypatch):
    """Replace the real Playwright executor with a deterministic fake."""
    calls = []

    async def fake_executor(url, actions, *args, **kwargs):
        calls.append({"url": url, "actions": actions})
        return {"success": True, "content": "md", "title": "t", "url": url}

    monkeypatch.setattr(
        "backend.modules.playwright_scraper.perform_actions_with_playwright",
        fake_executor,
    )
    # session_recorder imports lazily inside functions; patch its view too.
    monkeypatch.setattr(
        "backend.modules.session_recorder.perform_actions_with_playwright",
        fake_executor,
        raising=False,
    )
    return calls


STEPS = [
    {"action": "click", "selector": "#login-btn"},
    {"action": "type", "selector": "#email", "value": "a@b.c"},
]


# ── CRUD ────────────────────────────────────────────────────────────────────

class TestCrud:
    def test_create_and_get(self):
        rid = sr.create_recording("login-flow", "https://example.com")
        rec = sr.get_recording(rid)
        assert rec["name"] == "login-flow"
        assert rec["start_url"] == "https://example.com"
        assert rec["status"] == "recording"
        assert rec["steps"] == []

    def test_get_missing_returns_none(self):
        assert sr.get_recording(9999) is None

    def test_append_step_increments_count(self):
        rid = sr.create_recording("r", "https://example.com")
        sr.append_step(rid, {"index": 0, **STEPS[0]})
        sr.append_step(rid, {"index": 1, **STEPS[1]})
        rec = sr.get_recording(rid)
        assert rec["step_count"] == 2
        assert len(rec["steps"]) == 2
        assert rec["steps"][1]["selector"] == "#email"

    def test_append_to_missing_raises(self):
        with pytest.raises(ValueError):
            sr.append_step(9999, STEPS[0])

    def test_finish_sets_status_and_error(self):
        rid = sr.create_recording("r", "https://example.com")
        sr.finish_recording(rid, status="failed", duration_ms=12.5, error="boom")
        rec = sr.get_recording(rid)
        assert rec["status"] == "failed"
        assert rec["error"] == "boom"
        assert rec["duration_ms"] == 12.5

    def test_list_excludes_steps(self):
        rid = sr.create_recording("listed", "https://example.com")
        sr.append_step(rid, STEPS[0])
        rows = sr.list_recordings()
        assert any(r["id"] == rid and r["name"] == "listed" for r in rows)
        assert "steps" not in rows[0] or "steps_json" not in rows[0]

    def test_delete(self):
        rid = sr.create_recording("doomed", "https://example.com")
        assert sr.delete_recording(rid) is True
        assert sr.get_recording(rid) is None
        assert sr.delete_recording(rid) is False


# ── Record / Replay ────────────────────────────────────────────────────────

class TestRecordRun:
    @pytest.mark.asyncio
    async def test_records_all_steps_and_completes(self, _fake_playwright):
        result = await sr.record_run("https://example.com", STEPS, "my-flow")
        rid = result["recording_id"]
        rec = sr.get_recording(rid)
        assert rec["status"] == "completed"
        assert rec["step_count"] == 2
        assert [s["action"] for s in rec["steps"]] == ["click", "type"]
        # The executor saw the same actions we recorded.
        assert _fake_playwright[0]["actions"] == STEPS

    @pytest.mark.asyncio
    async def test_failure_is_persisted(self, monkeypatch):
        async def failing(url, actions, *a, **k):
            return {"success": False, "content": "", "title": "", "url": url, "error": "timeout"}
        monkeypatch.setattr(
            "backend.modules.playwright_scraper.perform_actions_with_playwright",
            failing,
        )
        result = await sr.record_run("https://example.com", STEPS)
        rec = sr.get_recording(result["recording_id"])
        assert rec["status"] == "failed"
        assert rec["error"] == "timeout"

    @pytest.mark.asyncio
    async def test_raising_executor_marks_failed_and_raises(self, monkeypatch):
        async def exploding(url, actions, *a, **k):
            raise RuntimeError("playwright gone")
        monkeypatch.setattr(
            "backend.modules.playwright_scraper.perform_actions_with_playwright",
            exploding,
        )
        with pytest.raises(RuntimeError):
            await sr.record_run("https://example.com", STEPS)
        rows = sr.list_recordings(limit=1)
        assert rows[0]["status"] == "failed"


class TestReplay:
    @pytest.mark.asyncio
    async def test_replays_recorded_steps(self, _fake_playwright):
        result = await sr.record_run("https://example.com", STEPS, "flow")
        replay = await sr.replay_recording(result["recording_id"])
        assert replay["success"] is True
        assert replay["replayed_steps"] == 2
        assert replay["recording_id"] == result["recording_id"]
        # Two executor invocations total: original run + replay.
        assert len(_fake_playwright) == 2
        assert _fake_playwright[1]["actions"] == STEPS
        assert [s["action"] for s in replay["step_results"]] == ["click", "type"]

    @pytest.mark.asyncio
    async def test_replay_missing_404(self):
        with pytest.raises(ValueError):
            await sr.replay_recording(424242)

    @pytest.mark.asyncio
    async def test_replay_of_in_progress_conflict(self):
        rid = sr.create_recording("partial", "https://example.com")
        with pytest.raises(ValueError):
            await sr.replay_recording(rid)
