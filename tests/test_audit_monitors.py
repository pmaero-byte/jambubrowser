"""
Tests for audit monitors — recurring audits with regression alerting.

Covers:
- Pure diff logic (baseline / new / resolved / persisting)
- Alert threshold selection (`fail_on`)
- Due-time scheduling math
- CRUD persistence
- run_monitor: baseline (no alert), regression (alert + webhook payload),
  resolution, and error handling
- Scheduler tick selection
- HTTP API: validation, CRUD, run-now, run history
"""
from __future__ import annotations

import pytest

from backend.modules import audit_monitor as am


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the database at a fresh temp file for every test."""
    db_path = str(tmp_path / "monitors.db")
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


def _finding(title, severity="high", employee="Security Auditor", category="csp"):
    return {
        "id": title,
        "employee": employee,
        "severity": severity,
        "category": category,
        "title": title,
        "description": f"{title} description",
        "fix_suggestion": f"fix {title}",
    }


def _done(findings):
    by_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {"findings": findings, "by_severity": by_sev, "url": "https://example.com"}


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

class TestComputeDiff:
    def test_baseline_marks_everything_new(self):
        diff = am.compute_diff([_finding("a"), _finding("b", "low")], None)
        assert diff["baseline"] is True
        assert len(diff["new"]) == 2
        assert diff["resolved"] == []
        assert diff["persisting"] == 0

    def test_new_resolved_and_persisting(self):
        previous = am.compute_diff([_finding("a"), _finding("b", "low")], None)
        prev_fps = set(previous["current_fingerprints"])
        diff = am.compute_diff([_finding("a"), _finding("c")], prev_fps)
        assert diff["baseline"] is False
        titles_new = {f["title"] for f in diff["new"]}
        assert titles_new == {"c"}
        assert diff["persisting"] == 1
        assert len(diff["resolved"]) == 1

    def test_fingerprint_ignores_id_changes(self):
        """A re-audit assigns new per-finding ids; the diff must not care."""
        prev = am.compute_diff([_finding("a")], None)
        changed_id = dict(_finding("a"), id="totally-new-id")
        diff = am.compute_diff([changed_id], set(prev["current_fingerprints"]))
        assert diff["new"] == []
        assert diff["persisting"] == 1


class TestAlertableFindings:
    def test_threshold_includes_higher_severities(self):
        findings = [
            _finding("c", "critical"),
            _finding("h", "high"),
            _finding("m", "medium"),
            _finding("l", "low"),
        ]
        alert = am.alertable_findings(findings, "high")
        assert {f["title"] for f in alert} == {"c", "h"}

    def test_none_never_alerts(self):
        assert am.alertable_findings([_finding("c", "critical")], "none") == []

    def test_critical_threshold_only_critical(self):
        findings = [_finding("c", "critical"), _finding("h", "high")]
        assert {f["title"] for f in am.alertable_findings(findings, "critical")} == {"c"}


class TestIsDue:
    def test_never_run_is_due(self):
        assert am.is_due({"enabled": True, "last_run_at": None, "interval_minutes": 60})

    def test_recent_run_not_due(self):
        import time
        now = time.time()
        assert not am.is_due(
            {"enabled": True, "last_run_at": now - 60, "interval_minutes": 60},
            now=now,
        )

    def test_old_run_is_due(self):
        import time
        now = time.time()
        assert am.is_due(
            {"enabled": True, "last_run_at": now - 3601, "interval_minutes": 60},
            now=now,
        )

    def test_disabled_never_due(self):
        assert not am.is_due({"enabled": False, "last_run_at": None, "interval_minutes": 5})


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestMonitorCrud:
    def test_create_and_get(self):
        m = am.create_monitor("https://example.com", mode="quick",
                              interval_minutes=60, fail_on="high")
        assert m["id"] > 0
        assert m["url"] == "https://example.com"
        assert m["enabled"] is True
        assert m["last_run_at"] is None
        assert am.get_monitor(m["id"])["url"] == "https://example.com"

    def test_list(self):
        am.create_monitor("https://a.com")
        am.create_monitor("https://b.com")
        assert len(am.list_monitors()) == 2

    def test_update_fields(self):
        m = am.create_monitor("https://a.com")
        updated = am.update_monitor(
            m["id"], interval_minutes=120, fail_on="critical", enabled=False,
        )
        assert updated["interval_minutes"] == 120
        assert updated["fail_on"] == "critical"
        assert updated["enabled"] is False

    def test_update_missing_returns_none(self):
        assert am.update_monitor(9999, interval_minutes=60) is None

    def test_delete(self):
        m = am.create_monitor("https://a.com")
        assert am.delete_monitor(m["id"]) is True
        assert am.get_monitor(m["id"]) is None
        assert am.delete_monitor(m["id"]) is False


# ---------------------------------------------------------------------------
# run_monitor
# ---------------------------------------------------------------------------

class TestRunMonitor:
    def _patch_audit(self, monkeypatch, done_payload):
        async def fake(url, mode):
            return done_payload
        monkeypatch.setattr(am, "_execute_audit", fake)

    def test_baseline_never_alerts(self, monkeypatch):
        m = am.create_monitor("https://example.com", fail_on="low")
        self._patch_audit(monkeypatch, _done([_finding("critical thing", "critical")]))

        alerted = []

        async def fake_notify(monitor, new_findings, resolved_count):
            alerted.append(new_findings)
        monkeypatch.setattr(am, "_notify_alert", fake_notify)

        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["status"] == "ok"
        assert result["baseline"] is True
        assert result["new_findings"] == 1
        assert result["alerted"] is False
        assert alerted == []  # baseline: no alert storm on setup
        # Run persisted
        runs = am.list_runs(m["id"])
        assert len(runs) == 1
        assert runs[0]["status"] == "ok"
        assert runs[0]["baseline"] is True
        # Monitor bookkeeping updated
        updated = am.get_monitor(m["id"])
        assert updated["last_status"] == "ok"
        assert updated["last_finding_count"] == 1

    def test_regression_alerts_and_webhook_payload(self, monkeypatch):
        m = am.create_monitor(
            "https://example.com", fail_on="high",
            webhook_url="https://hooks.example.com/jambu",
        )
        self._patch_audit(monkeypatch, _done([_finding("known", "low")]))
        asyncio_run(am.run_monitor(m["id"]))  # baseline

        captured = {}

        async def fake_send_webhook(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return True
        monkeypatch.setattr(am, "send_webhook", fake_send_webhook)

        async def fake_notifier_send(**kwargs):
            captured["notification"] = kwargs
        from backend.modules import notifications as notif_mod

        class FakeNotifier:
            async def send(self, **kwargs):
                await fake_notifier_send(**kwargs)
        monkeypatch.setattr(notif_mod, "get_notifier", lambda: FakeNotifier())

        self._patch_audit(monkeypatch, _done([
            _finding("known", "low"),
            _finding("new critical", "critical"),
        ]))
        result = asyncio_run(am.run_monitor(m["id"]))

        assert result["alerted"] is True
        assert result["new_findings"] == 1
        assert result["resolved_findings"] == 0
        assert [f["title"] for f in result["alert_findings"]] == ["new critical"]

        assert captured["url"] == "https://hooks.example.com/jambu"
        assert captured["payload"]["event"] == "audit.regression"
        assert captured["payload"]["new_findings"][0]["title"] == "new critical"
        assert captured["notification"]["category"] == "audit_monitor"
        # History: latest run is a regression, the first was the baseline.
        runs = am.list_runs(m["id"])
        assert runs[0]["baseline"] is False
        assert runs[-1]["baseline"] is True

    def test_below_threshold_regression_does_not_alert(self, monkeypatch):
        m = am.create_monitor("https://example.com", fail_on="critical")
        self._patch_audit(monkeypatch, _done([_finding("known", "low")]))
        asyncio_run(am.run_monitor(m["id"]))

        alerted = []

        async def fake_notify(monitor, new_findings, resolved_count):
            alerted.append(new_findings)
        monkeypatch.setattr(am, "_notify_alert", fake_notify)

        self._patch_audit(monkeypatch, _done([
            _finding("known", "low"),
            _finding("new medium", "medium"),
        ]))
        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["alerted"] is False
        assert alerted == []

    def test_resolution_is_counted_and_not_alerted(self, monkeypatch):
        m = am.create_monitor("https://example.com", fail_on="high")
        self._patch_audit(monkeypatch, _done([_finding("a", "critical")]))
        asyncio_run(am.run_monitor(m["id"]))

        alerted = []

        async def fake_notify(monitor, new_findings, resolved_count):
            alerted.append(resolved_count)
        monkeypatch.setattr(am, "_notify_alert", fake_notify)

        self._patch_audit(monkeypatch, _done([]))
        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["resolved_findings"] == 1
        assert result["alerted"] is False
        assert alerted == []

    def test_run_error_is_recorded(self, monkeypatch):
        m = am.create_monitor("https://example.com")

        async def boom(url, mode):
            raise RuntimeError("collection failed")
        monkeypatch.setattr(am, "_execute_audit", boom)

        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["status"] == "error"
        assert "collection failed" in result["error"]
        runs = am.list_runs(m["id"])
        assert runs[0]["status"] == "error"
        assert am.get_monitor(m["id"])["last_status"] == "error"

    def test_run_missing_monitor_raises(self):
        with pytest.raises(ValueError):
            asyncio_run(am.run_monitor(4242))


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class TestScheduler:
    def test_check_due_skips_recent_monitors(self, monkeypatch):
        due = am.create_monitor("https://due.example.com")
        recent = am.create_monitor("https://recent.example.com")

        async def fake(url, mode):
            return _done([])
        monkeypatch.setattr(am, "_execute_audit", fake)

        # First pass runs both (both never ran).
        scheduler = am.AuditMonitorScheduler(check_interval=1, initial_delay=0)
        ran = asyncio_run(scheduler.check_due_monitors())
        assert len(ran) == 2

        # Second pass runs neither: last_run_at is now, interval is a day.
        ran = asyncio_run(scheduler.check_due_monitors())
        assert ran == []

        # Back-date the "due" monitor → only it runs.
        from backend.core.database import get_db
        with get_db() as conn:
            conn.execute(
                "UPDATE audit_monitors SET last_run_at = 0 WHERE id = ?", (due["id"],)
            )
            conn.commit()
        ran = asyncio_run(scheduler.check_due_monitors())
        assert len(ran) == 1
        assert ran[0]["monitor_id"] == due["id"]
        assert recent["id"] != due["id"]  # sanity: distinct monitors


class TestSendWebhook:
    def test_refuses_unsafe_url(self):
        assert asyncio_run(am.send_webhook("http://127.0.0.1:9999/hook", {})) is False


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class TestMonitorApi:
    def test_create_and_list(self, client):
        resp = client.post("/audit/monitors", json={
            "url": "https://example.com", "mode": "quick",
            "interval_minutes": 120, "fail_on": "high",
        })
        assert resp.status_code == 200, resp.text
        monitor = resp.json()["monitor"]
        assert monitor["url"] == "https://example.com"
        assert monitor["interval_minutes"] == 120

        listed = client.get("/audit/monitors").json()
        assert listed["count"] == 1

    def test_create_validates_inputs(self, client):
        bad_payloads = [
            {"url": "http://localhost:9999"},              # SSRF-blocked
            {"url": "https://example.com", "mode": "turbo"},
            {"url": "https://example.com", "interval_minutes": 1},
            {"url": "https://example.com", "fail_on": "catastrophic"},
            {"url": "https://example.com", "webhook_url": "http://127.0.0.1/hook"},
        ]
        for payload in bad_payloads:
            resp = client.post("/audit/monitors", json=payload)
            assert resp.status_code == 422, f"{payload} -> {resp.status_code}"

    def test_patch_and_delete(self, client):
        mid = client.post("/audit/monitors", json={
            "url": "https://example.com",
        }).json()["monitor"]["id"]

        resp = client.patch(f"/audit/monitors/{mid}", json={
            "enabled": False, "fail_on": "critical",
        })
        assert resp.status_code == 200
        assert resp.json()["monitor"]["enabled"] is False

        assert client.delete(f"/audit/monitors/{mid}").status_code == 200
        assert client.get(f"/audit/monitors/{mid}").status_code == 404
        assert client.delete(f"/audit/monitors/{mid}").status_code == 404

    def test_run_now_returns_diff_and_history(self, client, monkeypatch):
        from backend.modules import audit_monitor as am_mod

        async def fake(url, mode):
            return _done([_finding("missing csp", "high")])
        monkeypatch.setattr(am_mod, "_execute_audit", fake)

        mid = client.post("/audit/monitors", json={
            "url": "https://example.com",
        }).json()["monitor"]["id"]

        resp = client.post(f"/audit/monitors/{mid}/run")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["baseline"] is True
        assert body["total_findings"] == 1

        runs = client.get(f"/audit/monitors/{mid}/runs").json()
        assert runs["count"] == 1
        assert runs["runs"][0]["new_findings"] == 1

    def test_run_missing_monitor_404(self, client):
        assert client.post("/audit/monitors/9999/run").status_code == 404
        assert client.get("/audit/monitors/9999/runs").status_code == 404

    def test_check_now_runs_due_monitors(self, client, monkeypatch):
        from backend.modules import audit_monitor as am_mod

        async def fake(url, mode):
            return _done([])
        monkeypatch.setattr(am_mod, "_execute_audit", fake)

        client.post("/audit/monitors", json={"url": "https://example.com"})
        resp = client.post("/audit/monitors/check-now")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1


# ---------------------------------------------------------------------------
# Visual regression
# ---------------------------------------------------------------------------

def _b64_png(color, size=(32, 24)) -> str:
    """Encode a solid-color PNG to base64 (no fixture files)."""
    import base64
    import io

    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class TestVisualRegression:
    def _patch_audit_with_screenshot(self, monkeypatch, screenshot):
        async def fake(url, mode):
            done = _done([])
            done["_screenshot_base64"] = screenshot
            return done
        monkeypatch.setattr(am, "_execute_audit", fake)

    def test_baseline_stores_screenshot_without_alert(self, monkeypatch):
        m = am.create_monitor("https://example.com")
        self._patch_audit_with_screenshot(monkeypatch, _b64_png((0, 0, 0)))
        visual_alerts = []

        async def fake_visual(monitor, pct):
            visual_alerts.append(pct)
        monkeypatch.setattr(am, "_notify_visual", fake_visual)

        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["baseline"] is True
        assert result["visual_change_pct"] is None  # no previous screenshot
        assert result["visual_changed"] is False
        assert visual_alerts == []
        assert am.list_runs(m["id"])[0]["has_screenshot"] is True

    def test_changed_page_alerts_visually(self, monkeypatch):
        m = am.create_monitor("https://example.com", visual_threshold_pct=2.0)
        self._patch_audit_with_screenshot(monkeypatch, _b64_png((0, 0, 0)))
        asyncio_run(am.run_monitor(m["id"]))  # baseline

        visual_alerts = []

        async def fake_visual(monitor, pct):
            visual_alerts.append(pct)
        monkeypatch.setattr(am, "_notify_visual", fake_visual)

        self._patch_audit_with_screenshot(monkeypatch, _b64_png((255, 255, 255)))
        result = asyncio_run(am.run_monitor(m["id"]))

        assert result["visual_change_pct"] == 100.0
        assert result["visual_changed"] is True
        assert result["visual_alerted"] is True
        assert visual_alerts == [100.0]
        assert am.list_runs(m["id"])[0]["visual_change_pct"] == 100.0

    def test_unchanged_page_does_not_alert(self, monkeypatch):
        m = am.create_monitor("https://example.com")
        png = _b64_png((10, 20, 30))
        self._patch_audit_with_screenshot(monkeypatch, png)
        asyncio_run(am.run_monitor(m["id"]))

        visual_alerts = []

        async def fake_visual(monitor, pct):
            visual_alerts.append(pct)
        monkeypatch.setattr(am, "_notify_visual", fake_visual)

        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["visual_change_pct"] == 0.0
        assert result["visual_changed"] is False
        assert visual_alerts == []

    def test_zero_threshold_disables_alerts_but_still_records(self, monkeypatch):
        m = am.create_monitor("https://example.com", visual_threshold_pct=0)
        self._patch_audit_with_screenshot(monkeypatch, _b64_png((0, 0, 0)))
        asyncio_run(am.run_monitor(m["id"]))

        visual_alerts = []

        async def fake_visual(monitor, pct):
            visual_alerts.append(pct)
        monkeypatch.setattr(am, "_notify_visual", fake_visual)

        self._patch_audit_with_screenshot(monkeypatch, _b64_png((255, 255, 255)))
        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["visual_change_pct"] == 100.0  # recorded
        assert result["visual_changed"] is False
        assert visual_alerts == []

    def test_missing_screenshot_degrades_gracefully(self, monkeypatch):
        m = am.create_monitor("https://example.com")

        async def fake(url, mode):
            return _done([])  # no _screenshot_base64 key
        monkeypatch.setattr(am, "_execute_audit", fake)

        result = asyncio_run(am.run_monitor(m["id"]))
        assert result["visual_change_pct"] is None
        assert result["visual_changed"] is False

    def test_webhook_visual_payload(self, monkeypatch):
        m = am.create_monitor(
            "https://example.com", webhook_url="https://hooks.example.com/x",
        )
        self._patch_audit_with_screenshot(monkeypatch, _b64_png((0, 0, 0)))
        asyncio_run(am.run_monitor(m["id"]))

        # Keep the real _notify_visual (webhook path) but stub the desktop
        # notifier so tests don't fire OS notifications.
        from backend.modules import notifications as notif_mod

        class FakeNotifier:
            async def send(self, **kwargs):
                return None
        monkeypatch.setattr(notif_mod, "get_notifier", lambda: FakeNotifier())

        captured = {}

        async def fake_send_webhook(url, payload):
            captured.update(payload)
            return True
        monkeypatch.setattr(am, "send_webhook", fake_send_webhook)

        self._patch_audit_with_screenshot(monkeypatch, _b64_png((255, 255, 255)))
        asyncio_run(am.run_monitor(m["id"]))

        assert captured["event"] == "audit.visual_change"
        assert captured["visual_change_pct"] == 100.0
        assert captured["visual_threshold_pct"] == 2.0

    def test_runs_pruned_to_keep(self, monkeypatch):
        m = am.create_monitor("https://example.com")
        self._patch_audit_with_screenshot(monkeypatch, _b64_png((0, 0, 0), size=(2, 2)))
        for _ in range(am.RUN_HISTORY_KEEP + 3):
            asyncio_run(am.run_monitor(m["id"]))
        runs = am.list_runs(m["id"], limit=100)
        assert len(runs) == am.RUN_HISTORY_KEEP


class TestVisualApi:
    def test_create_with_visual_threshold(self, client):
        resp = client.post("/audit/monitors", json={
            "url": "https://example.com", "visual_threshold_pct": 5.0,
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["monitor"]["visual_threshold_pct"] == 5.0

    def test_default_visual_threshold(self, client):
        resp = client.post("/audit/monitors", json={"url": "https://example.com"})
        assert resp.json()["monitor"]["visual_threshold_pct"] == 2.0

    def test_negative_threshold_rejected(self, client):
        resp = client.post("/audit/monitors", json={
            "url": "https://example.com", "visual_threshold_pct": -1,
        })
        assert resp.status_code == 422

    def test_patch_visual_threshold(self, client):
        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        resp = client.patch(
            f"/audit/monitors/{mid}", json={"visual_threshold_pct": 7.5},
        )
        assert resp.status_code == 200
        assert resp.json()["monitor"]["visual_threshold_pct"] == 7.5


class TestScreenshotApi:
    def _store_run(self, monitor_id: int, screenshot_b64=None) -> int:
        am._persist_run(monitor_id, status="ok", screenshot_b64=screenshot_b64)
        runs = am.list_runs(monitor_id)
        assert runs, "run was not persisted"
        return runs[0]["id"]

    def test_screenshot_round_trips_as_png(self, client):
        import base64

        png_b64 = _b64_png((200, 30, 30))
        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        rid = self._store_run(mid, png_b64)

        resp = client.get(f"/audit/monitors/{mid}/runs/{rid}/screenshot")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == base64.b64decode(png_b64)

    def test_unknown_monitor_404(self, client):
        resp = client.get("/audit/monitors/9999/runs/1/screenshot")
        assert resp.status_code == 404

    def test_unknown_run_404(self, client):
        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        resp = client.get(f"/audit/monitors/{mid}/runs/9999/screenshot")
        assert resp.status_code == 404

    def test_run_without_screenshot_404(self, client):
        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        rid = self._store_run(mid, None)
        resp = client.get(f"/audit/monitors/{mid}/runs/{rid}/screenshot")
        assert resp.status_code == 404

    def test_run_of_other_monitor_404(self, client):
        mid_a = client.post(
            "/audit/monitors", json={"url": "https://a.example.com"},
        ).json()["monitor"]["id"]
        mid_b = client.post(
            "/audit/monitors", json={"url": "https://b.example.com"},
        ).json()["monitor"]["id"]
        rid = self._store_run(mid_a, _b64_png((0, 0, 0)))
        resp = client.get(f"/audit/monitors/{mid_b}/runs/{rid}/screenshot")
        assert resp.status_code == 404

    def test_corrupt_stored_data_500(self, client):
        import base64

        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        not_png = base64.b64encode(b"this is not a png").decode()
        rid = self._store_run(mid, not_png)
        resp = client.get(f"/audit/monitors/{mid}/runs/{rid}/screenshot")
        assert resp.status_code == 500

    def test_store_getter_scoping(self):
        m = am.create_monitor("https://example.com")
        assert am.get_run_screenshot(m["id"], 424242) is None
        rid = self._store_run(m["id"], _b64_png((1, 2, 3)))
        assert am.get_run_screenshot(m["id"] + 999, rid) is None
        assert am.get_run_screenshot(m["id"], rid) == _b64_png((1, 2, 3))


class TestRunDiffApi:
    def _two_runs(self, client, first, second):
        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        am._persist_run(mid, status="ok", screenshot_b64=first)
        am._persist_run(mid, status="ok", screenshot_b64=second)
        first_id, second_id = [
            r["id"] for r in reversed(am.list_runs(mid))
        ]
        return mid, first_id, second_id

    def test_diff_renders_red_where_pixels_changed(self, client):
        import base64
        import io

        from PIL import Image

        mid, _, second_id = self._two_runs(
            client, _b64_png((0, 0, 0)), _b64_png((255, 255, 255)),
        )
        resp = client.get(f"/audit/monitors/{mid}/runs/{second_id}/diff")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "image/png"
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        reds = sum(1 for px in img.getdata() if px == (255, 0, 0))
        assert reds == img.size[0] * img.size[1]  # everything changed
        assert base64.b64encode(resp.content)  # valid base64-able bytes

    def test_baseline_run_has_no_diff(self, client):
        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        am._persist_run(mid, status="ok", screenshot_b64=_b64_png((0, 0, 0)))
        rid = am.list_runs(mid)[0]["id"]
        resp = client.get(f"/audit/monitors/{mid}/runs/{rid}/diff")
        assert resp.status_code == 404

    def test_run_without_screenshot_has_no_diff(self, client):
        mid, _, second_id = self._two_runs(
            client, _b64_png((0, 0, 0)), _b64_png((255, 255, 255)),
        )
        # A later run with no screenshot of its own → no diff.
        am._persist_run(mid, status="ok", screenshot_b64=None)
        latest = am.list_runs(mid)[0]["id"]
        assert latest != second_id
        resp = client.get(f"/audit/monitors/{mid}/runs/{latest}/diff")
        assert resp.status_code == 404

    def test_unknown_monitor_and_run_404(self, client):
        assert client.get("/audit/monitors/9999/runs/1/diff").status_code == 404
        mid = client.post(
            "/audit/monitors", json={"url": "https://example.com"},
        ).json()["monitor"]["id"]
        assert client.get(
            f"/audit/monitors/{mid}/runs/9999/diff",
        ).status_code == 404

    def test_diff_pair_scoping(self):
        a = am.create_monitor("https://a.example.com")
        b = am.create_monitor("https://b.example.com")
        am._persist_run(a["id"], status="ok", screenshot_b64=_b64_png((0, 0, 0)))
        am._persist_run(a["id"], status="ok", screenshot_b64=_b64_png((1, 1, 1)))
        second = am.list_runs(a["id"])[0]["id"]
        # Other monitor's id → no pair (no cross-monitor leak).
        assert am.get_run_diff_pair(b["id"], second) is None
        prev, curr = am.get_run_diff_pair(a["id"], second)
        assert prev == _b64_png((0, 0, 0))
        assert curr == _b64_png((1, 1, 1))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def asyncio_run(coro):
    """Run a coroutine to completion in a fresh event loop."""
    import asyncio
    return asyncio.run(coro)
