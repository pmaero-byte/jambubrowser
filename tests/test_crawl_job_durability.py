"""
Durability of crawl/batch jobs.

The point of these is that a job survives the process: status and results are
readable after a restart, and a job the process died inside is not left lying at
"running" forever.
"""

import asyncio
import time

import pytest


@pytest.fixture(autouse=True)
def clean_jobs():
    from backend.modules.crawl_jobs import clear_job_store, reset_crawl_jobs
    reset_crawl_jobs()
    clear_job_store()
    yield
    reset_crawl_jobs()
    clear_job_store()


def _fetch(body="<p>content</p>"):
    async def fetch(url, max_age=0.0):
        return (200, body)
    return fetch


def _config(**kw):
    from backend.modules.webgraph import CrawlConfig
    return CrawlConfig(**kw)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TestJobStore:
    def test_submit_writes_a_row_immediately(self):
        """Status must be durable from the moment the job exists, otherwise a
        crash between submit and completion loses the job entirely."""
        from backend.modules.crawl_jobs import get_crawl_jobs, load_job

        async def go():
            m = get_crawl_jobs()
            job = m.submit("https://d.test/", _fetch(), _config(page_limit=2))
            stored = load_job(job.id)
            await m.wait(job.id, timeout=5)
            return job, stored

        job, stored = asyncio.run(go())
        assert stored is not None
        assert stored.id == job.id

    def test_completed_job_and_results_survive_a_new_manager(self):
        """Simulates a restart: a fresh manager must still answer for the job."""
        from backend.modules.crawl_jobs import (
            CrawlJobManager, get_crawl_jobs, load_job, reset_crawl_jobs,
        )

        async def run():
            m = get_crawl_jobs()
            job = m.submit("https://d.test/", _fetch(), _config(page_limit=1))
            await m.wait(job.id, timeout=5)
            return job.id

        job_id = asyncio.run(run())

        reset_crawl_jobs()  # process "restarts"; the DB does not
        fresh = CrawlJobManager()
        restored = fresh.get(job_id)
        assert restored is not None
        assert restored.status.value == "completed"
        assert len(restored.pages) >= 1
        assert restored.pages[0].text
        assert len(restored.pages[0].receipt.content_sha256) == 64

    def test_batch_results_survive_a_restart(self):
        from backend.modules.crawl_jobs import get_crawl_jobs, reset_crawl_jobs, CrawlJobManager

        async def run():
            m = get_crawl_jobs()
            job = m.submit_batch(["https://d.test/a", "https://d.test/b"], _fetch())
            await m.wait(job.id, timeout=5)
            return job.id

        job_id = asyncio.run(run())
        reset_crawl_jobs()
        restored = CrawlJobManager().get(job_id)
        assert restored is not None
        assert restored.total == 2
        assert len(restored.results) == 2

    def test_stored_page_text_is_capped(self):
        """Results are already returned to the caller; the persisted copy exists
        so a post-restart poll is not empty, not so it can hold everything."""
        from backend.modules.crawl_jobs import _STORED_TEXT_CHARS, get_crawl_jobs, reset_crawl_jobs

        huge = "<p>{}</p>".format("word " * 20000)

        async def run():
            m = get_crawl_jobs()
            job = m.submit("https://d.test/", _fetch(huge), _config(page_limit=1))
            await m.wait(job.id, timeout=5)
            return job.id

        job_id = asyncio.run(run())
        reset_crawl_jobs()
        from backend.modules.crawl_jobs import CrawlJobManager
        restored = CrawlJobManager().get(job_id)
        assert len(restored.pages[0].text) <= _STORED_TEXT_CHARS

    def test_orphaned_running_job_becomes_interrupted(self):
        """A job the process died inside must reach a terminal state, or a
        poller waits forever on work that will never advance."""
        from backend.modules.crawl_jobs import (
            JobStatus, recover_orphaned,
        )
        from backend.core.database import get_db

        with get_db() as conn:
            conn.execute(
                "INSERT INTO crawl_jobs (id, kind, target, status, total, completed, "
                "created_at) VALUES (?, 'crawl', '{}', ?, 10, 3, ?)",
                ("crawl_orphan", JobStatus.RUNNING.value, time.time()),
            )
            conn.commit()

        n = recover_orphaned()
        assert n == 1

        from backend.modules.crawl_jobs import load_job
        recovered = load_job("crawl_orphan")
        assert recovered.status is JobStatus.INTERRUPTED
        assert recovered.completed_at is not None

    def test_recovery_is_idempotent(self):
        from backend.modules.crawl_jobs import recover_orphaned
        assert recover_orphaned() == 0
        assert recover_orphaned() == 0

    def test_finished_jobs_are_not_touched_by_recovery(self):
        from backend.modules.crawl_jobs import JobStatus, recover_orphaned
        from backend.core.database import get_db

        with get_db() as conn:
            conn.execute(
                "INSERT INTO crawl_jobs (id, kind, target, status, total, completed, "
                "created_at) VALUES (?, 'crawl', '{}', ?, 1, 1, ?)",
                ("crawl_done", JobStatus.COMPLETED.value, time.time()),
            )
            conn.commit()

        recover_orphaned()
        from backend.modules.crawl_jobs import load_job
        assert load_job("crawl_done").status is JobStatus.COMPLETED

    def test_expired_rows_are_purged(self):
        from backend.modules.crawl_jobs import purge_expired, load_job
        from backend.core.database import get_db

        with get_db() as conn:
            conn.execute(
                "INSERT INTO crawl_jobs (id, kind, target, status, total, completed, "
                "created_at, completed_at, expires_at) "
                "VALUES ('crawl_old', 'crawl', '{}', 'completed', 1, 1, ?, ?, ?)",
                (time.time() - 10000, time.time() - 9999, time.time() - 5000),
            )
            conn.commit()

        assert purge_expired() >= 1
        assert load_job("crawl_old") is None

    def test_live_rows_are_not_purged(self):
        from backend.modules.crawl_jobs import purge_expired, load_job
        from backend.core.database import get_db

        with get_db() as conn:
            conn.execute(
                "INSERT INTO crawl_jobs (id, kind, target, status, total, completed, "
                "created_at, completed_at, expires_at) "
                "VALUES ('crawl_future', 'crawl', '{}', 'completed', 1, 1, ?, ?, ?)",
                (time.time(), time.time(), time.time() + 9999),
            )
            conn.commit()

        purge_expired()
        assert load_job("crawl_future") is not None

    def test_corrupt_row_does_not_crash_the_reader(self):
        """A malformed row must not break the whole job list."""
        from backend.modules.crawl_jobs import load_job, load_jobs
        from backend.core.database import get_db

        with get_db() as conn:
            conn.execute(
                "INSERT INTO crawl_jobs (id, kind, target, status, total, completed, "
                "created_at, meta, results) VALUES "
                "('crawl_bad', 'crawl', 'not-json', 'completed', 1, 1, ?, 'not-json', 'not-json')",
                (time.time(),),
            )
            conn.commit()

        assert load_job("crawl_bad") is not None  # degrades, does not raise
        assert isinstance(load_jobs(), list)

    def test_unknown_status_is_read_as_interrupted(self):
        from backend.modules.crawl_jobs import JobStatus, load_job
        from backend.core.database import get_db

        with get_db() as conn:
            conn.execute(
                "INSERT INTO crawl_jobs (id, kind, target, status, total, completed, created_at) "
                "VALUES ('crawl_weird', 'crawl', '{}', 'quantum', 1, 0, ?)",
                (time.time(),),
            )
            conn.commit()

        assert load_job("crawl_weird").status is JobStatus.INTERRUPTED


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------

class TestDurableJobsViaApi:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.core.api_keys import create_api_key
        from backend.engine import app
        raw, _ = create_api_key("durable-tests")
        monkeypatch.delenv("JAMBU_WEBDATA_AUTH", raising=False)
        with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
            yield c

    def test_status_is_readable_after_the_manager_is_reset(self, client, monkeypatch):
        """The API must keep answering for a job whose in-memory record is gone."""
        from backend.modules.crawl_jobs import get_crawl_jobs, reset_crawl_jobs
        from backend.routes import webdata

        async def fetch(url, max_age=0.0):
            return (200, "<p>page</p>")

        monkeypatch.setattr(webdata, "_http_fetch", fetch)
        job = client.post("/v1/crawl", json={"url": "https://d.test/", "page_limit": 2}).json()

        import time as _t
        deadline = _t.time() + 10
        while _t.time() < deadline:
            if client.get(f"/v1/crawl/{job['id']}").json()["status"] == "completed":
                break
            _t.sleep(0.02)

        # Drop every in-memory record; only the database remains.
        reset_crawl_jobs()

        body = client.get(f"/v1/crawl/{job['id']}").json()
        assert body["status"] == "completed"
        assert body["completed"] >= 1
        assert body["data"]

    def test_list_includes_durable_only_jobs(self, client, monkeypatch):
        from backend.modules.crawl_jobs import reset_crawl_jobs
        from backend.routes import webdata

        async def fetch(url, max_age=0.0):
            return (200, "<p>page</p>")

        monkeypatch.setattr(webdata, "_http_fetch", fetch)
        job = client.post("/v1/crawl", json={"url": "https://d.test/", "page_limit": 1}).json()

        import time as _t
        deadline = _t.time() + 10
        while _t.time() < deadline:
            if client.get(f"/v1/crawl/{job['id']}").json()["status"] in ("completed", "failed", "cancelled"):
                break
            _t.sleep(0.02)

        reset_crawl_jobs()
        listing = client.get("/v1/crawl").json()
        assert any(j["id"] == job["id"] for j in listing["jobs"])
