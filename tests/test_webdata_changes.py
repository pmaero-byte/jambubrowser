"""
Batch scraping and content change tracking.

Both are durable-ish features over the web-data surface: a batch is a bounded
set of concurrent fetches, and change tracking compares a page to its own past.
"""

import asyncio
import time

import pytest


@pytest.fixture(autouse=True)
def clean_crawl_jobs():
    from backend.modules.crawl_jobs import reset_crawl_jobs
    reset_crawl_jobs()
    yield
    reset_crawl_jobs()


@pytest.fixture(autouse=True)
def clean_snapshots():
    """Change tracking is durable, so tests must not inherit each other's pages."""
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute("DELETE FROM page_snapshots")
        conn.execute("DELETE FROM page_changes")
        conn.commit()
    yield


@pytest.fixture
def api_key():
    from backend.core.api_keys import create_api_key
    raw, _ = create_api_key("webdata-changes-tests")
    return raw


@pytest.fixture
def client(api_key, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.engine import app
    monkeypatch.delenv("JAMBU_WEBDATA_AUTH", raising=False)
    with TestClient(app, headers={"Authorization": f"Bearer {api_key}"}) as c:
        yield c


def _poll_batch(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/v1/batch/{job_id}").json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError("batch job did not finish")


# ---------------------------------------------------------------------------
# Diff primitives
# ---------------------------------------------------------------------------

class TestDiffPrimitives:
    def test_hash_is_stable_and_content_sensitive(self):
        from backend.modules.changes import content_hash
        assert content_hash("abc") == content_hash("abc")
        assert content_hash("abc") != content_hash("abd")
        assert len(content_hash("")) == 64

    def test_identical_text_has_no_diff(self):
        from backend.modules.changes import unified_diff
        assert unified_diff("a\nb", "a\nb") == []

    def test_diff_reports_additions_and_removals(self):
        from backend.modules.changes import summarise_diff, unified_diff
        d = unified_diff("one\ntwo\nthree", "one\nTWO\nthree")
        assert d
        stats = summarise_diff(d)
        assert stats["added_lines"] == 1 and stats["removed_lines"] == 1

    def test_diff_is_bounded(self):
        from backend.modules.changes import MAX_DIFF_LINES, unified_diff
        old = "\n".join(f"line {i}" for i in range(2000))
        new = "\n".join(f"changed {i}" for i in range(2000))
        d = unified_diff(old, new)
        assert len(d) <= MAX_DIFF_LINES + 1
        assert "truncated" in d[-1]


# ---------------------------------------------------------------------------
# Change tracking
# ---------------------------------------------------------------------------

class TestChangeTracking:
    def test_first_check_is_recorded_but_not_a_change(self):
        """Alerting on the first sighting would fire once for every new URL."""
        from backend.modules.changes import record_check
        d = record_check("https://c.test/a", "hello world")
        assert d.is_first_check is True
        assert d.changed is False
        assert d.hash_before is None
        assert d.hash_after == d.hash_after.lower()

    def test_unchanged_content_is_not_reported(self):
        from backend.modules.changes import record_check
        record_check("https://c.test/b", "same text")
        d = record_check("https://c.test/b", "same text")
        assert d.is_first_check is False
        assert d.changed is False
        assert d.diff == []

    def test_changed_content_reports_the_delta(self):
        from backend.modules.changes import record_check
        record_check("https://c.test/c", "price: 10\nstock: yes")
        d = record_check("https://c.test/c", "price: 12\nstock: yes")
        assert d.changed is True
        assert d.hash_before != d.hash_after
        assert d.added_lines == 1 and d.removed_lines == 1
        assert any("price: 12" in line for line in d.diff)

    def test_snapshot_is_retrievable_and_updates(self):
        from backend.modules.changes import get_snapshot, record_check
        url = "https://c.test/d"
        record_check(url, "v1")
        assert get_snapshot(url)["content_sha256"]
        record_check(url, "v2")
        snap = get_snapshot(url)
        assert snap["check_count"] == 2
        assert snap["change_count"] == 1
        assert snap["text"] == "v2"

    def test_watch_list_and_recent_changes(self):
        from backend.modules.changes import list_watched, recent_changes, record_check
        record_check("https://c.test/e", "a")
        record_check("https://c.test/e", "b")
        assert any(w["url"] == "https://c.test/e" for w in list_watched())
        changes = recent_changes("https://c.test/e")
        assert len(changes) == 1
        assert changes[0]["added_lines"] >= 1

    def test_forget_removes_snapshot_and_history(self):
        from backend.modules.changes import forget, get_snapshot, recent_changes, record_check
        url = "https://c.test/f"
        record_check(url, "a")
        record_check(url, "b")
        assert forget(url) is True
        assert get_snapshot(url) is None
        assert recent_changes(url) == []

    def test_forget_works_without_change_history(self):
        """Regression: forget() read `rowcount` after a *second* DELETE, so it
        reported "not found" for any page that had never changed."""
        from backend.modules.changes import forget, get_snapshot, record_check
        url = "https://c.test/no-history"
        record_check(url, "only version")   # snapshot, but no page_changes rows
        assert get_snapshot(url) is not None
        assert forget(url) is True
        assert get_snapshot(url) is None

    def test_forget_unknown_url_is_false(self):
        from backend.modules.changes import forget
        assert forget("https://c.test/never-seen") is False


class TestChangeEndpoints:
    def _fake(self, monkeypatch, bodies):
        """Serve a scripted sequence of bodies, one per call."""
        from backend.routes import webdata
        state = {"i": 0}

        async def fetch(url, max_age=0.0):
            i = min(state["i"], len(bodies) - 1)
            state["i"] += 1
            return (200, bodies[i])

        monkeypatch.setattr(webdata, "_http_fetch", fetch)
        return state

    def test_check_reports_first_then_change(self, client, monkeypatch):
        self._fake(monkeypatch, ["version one", "version two"])

        first = client.post("/v1/changes/check", json={"url": "https://ch.test/x"}).json()
        assert first["first_check"] is True and first["changed"] is False

        second = client.post("/v1/changes/check", json={"url": "https://ch.test/x"}).json()
        assert second["first_check"] is False
        assert second["changed"] is True
        assert second["diff"]

    def test_check_can_omit_the_diff(self, client, monkeypatch):
        self._fake(monkeypatch, ["a", "b"])
        client.post("/v1/changes/check", json={"url": "https://ch.test/y"})
        body = client.post("/v1/changes/check", json={"url": "https://ch.test/y",
                                                     "include_diff": False}).json()
        assert body["changed"] is True
        assert "diff" not in body

    def test_list_watched_and_change_log(self, client, monkeypatch):
        self._fake(monkeypatch, ["a", "b"])
        client.post("/v1/changes/check", json={"url": "https://ch.test/z"})
        client.post("/v1/changes/check", json={"url": "https://ch.test/z"})

        watched = client.get("/v1/changes").json()
        assert any(w["url"] == "https://ch.test/z" for w in watched["watched"])

        log = client.get("/v1/changes", params={"url": "https://ch.test/z"}).json()
        assert len(log["changes"]) == 1

    def test_forget_endpoint(self, client, monkeypatch):
        self._fake(monkeypatch, ["a"])
        client.post("/v1/changes/check", json={"url": "https://ch.test/w"})
        r = client.request("DELETE", "/v1/changes", json={"url": "https://ch.test/w"})
        assert r.json()["forgotten"] is True

    def test_check_rejects_unsafe_url(self, client, monkeypatch):
        self._fake(monkeypatch, ["x"])
        r = client.post("/v1/changes/check", json={"url": "http://127.0.0.1/"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

class TestBatch:
    def _fake(self, monkeypatch):
        from backend.routes import webdata

        async def fetch(url, max_age=0.0):
            if url.endswith("/boom"):
                raise RuntimeError("nope")
            return (200, f"<p>content of {url}</p>")

        monkeypatch.setattr(webdata, "_http_fetch", fetch)

    def test_batch_returns_a_job_immediately(self, client, monkeypatch):
        self._fake(monkeypatch)
        r = client.post("/v1/batch", json={"urls": ["https://b.test/1", "https://b.test/2"]})
        body = r.json()
        assert body["total"] == 2
        assert body["id"].startswith("batch_")
        assert body["poll"] == f"/v1/batch/{body['id']}"

    def test_batch_completes_with_per_url_results(self, client, monkeypatch):
        self._fake(monkeypatch)
        job = client.post("/v1/batch", json={
            "urls": ["https://b.test/1", "https://b.test/2", "https://b.test/3"],
        }).json()
        done = _poll_batch(client, job["id"])
        assert done["status"] == "completed"
        assert done["total"] == 3 and done["completed"] == 3
        urls = {r["url"] for r in done["data"]}
        assert urls == {"https://b.test/1", "https://b.test/2", "https://b.test/3"}
        for r in done["data"]:
            assert len(r["receipt"]["content_sha256"]) == 64

    def test_one_bad_url_does_not_fail_the_batch(self, client, monkeypatch):
        self._fake(monkeypatch)
        job = client.post("/v1/batch", json={
            "urls": ["https://b.test/ok", "https://b.test/boom"],
        }).json()
        done = _poll_batch(client, job["id"])
        assert done["status"] == "completed"
        assert done["completed"] == 1
        assert done["errors"] and "boom" in done["errors"][0]["url"]

    def test_batch_rejects_unsafe_urls_up_front(self, client, monkeypatch):
        self._fake(monkeypatch)
        r = client.post("/v1/batch", json={"urls": ["https://b.test/ok", "http://10.0.0.1/"]})
        assert r.status_code == 400

    def test_batch_requires_urls(self, client, monkeypatch):
        self._fake(monkeypatch)
        assert client.post("/v1/batch", json={"urls": []}).status_code == 422

    def test_unknown_batch_is_404(self, client, monkeypatch):
        self._fake(monkeypatch)
        assert client.get("/v1/batch/batch_missing").status_code == 404

    def test_batch_is_distinct_from_a_crawl_job(self, client, monkeypatch):
        """A crawl job id must not be readable through the batch endpoint."""
        self._fake(monkeypatch)
        crawl = client.post("/v1/crawl", json={"url": "https://b.test/1", "page_limit": 2}).json()
        assert client.get(f"/v1/batch/{crawl['id']}").status_code == 404

    def test_batch_requires_auth(self, monkeypatch):
        from fastapi.testclient import TestClient
        from backend.engine import app
        monkeypatch.delenv("JAMBU_WEBDATA_AUTH", raising=False)
        with TestClient(app) as c:
            assert c.post("/v1/batch", json={"urls": ["https://b.test/1"]}).status_code == 401
