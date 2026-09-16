"""
Audit monitors — recurring audits with regression alerting.

A monitor re-runs the audit pipeline on an interval and diffs the active
findings against the previous run:

- **new**      findings whose content fingerprint wasn't in the last run
- **resolved** fingerprints from the last run that are gone now
- **persisting** the rest

A run alerts (desktop notification + optional webhook) only when *new*
findings at or above the monitor's ``fail_on`` severity appear. The first
run establishes the baseline and never alerts — otherwise every monitor
would fire on setup day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from backend.core.database import get_db

log = logging.getLogger("jambu.audit_monitor")

SEVERITIES = ("critical", "high", "medium", "low", "info")
DEFAULT_INTERVAL_MINUTES = 1440  # daily
MIN_INTERVAL_MINUTES = 5
DEFAULT_CHECK_INTERVAL = 60  # scheduler tick, seconds
DEFAULT_VISUAL_THRESHOLD_PCT = 2.0
RUN_HISTORY_KEEP = 20  # runs retained per monitor (screenshots are heavy)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def _row_to_monitor(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "mode": row["mode"],
        "interval_minutes": row["interval_minutes"],
        "fail_on": row["fail_on"],
        "webhook_url": row["webhook_url"],
        "enabled": bool(row["enabled"]),
        "visual_threshold_pct": row["visual_threshold_pct"],
        "created_at": row["created_at"],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"],
        "last_finding_count": row["last_finding_count"],
        "last_error": row["last_error"],
    }


def create_monitor(
    url: str,
    mode: str = "quick",
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    fail_on: str = "high",
    webhook_url: Optional[str] = None,
    enabled: bool = True,
    visual_threshold_pct: float = DEFAULT_VISUAL_THRESHOLD_PCT,
) -> dict:
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO audit_monitors
                (url, mode, interval_minutes, fail_on, webhook_url, enabled,
                 visual_threshold_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                url, mode, interval_minutes, fail_on, webhook_url,
                1 if enabled else 0, visual_threshold_pct,
            ),
        )
        conn.commit()
        monitor_id = cur.lastrowid
    return get_monitor(monitor_id)


def get_monitor(monitor_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM audit_monitors WHERE id = ?", (monitor_id,)
        ).fetchone()
    return _row_to_monitor(row) if row else None


def list_monitors() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM audit_monitors ORDER BY id").fetchall()
    return [_row_to_monitor(r) for r in rows]


def update_monitor(monitor_id: int, **fields) -> Optional[dict]:
    allowed = {
        "url", "mode", "interval_minutes", "fail_on", "webhook_url",
        "enabled", "visual_threshold_pct",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    if not updates:
        return get_monitor(monitor_id)

    assignments = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [monitor_id]
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE audit_monitors SET {assignments} WHERE id = ?", values
        )
        conn.commit()
    if cur.rowcount == 0:
        return None
    return get_monitor(monitor_id)


def delete_monitor(monitor_id: int) -> bool:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM audit_monitor_runs WHERE monitor_id = ?", (monitor_id,)
        )
        cur = conn.execute("DELETE FROM audit_monitors WHERE id = ?", (monitor_id,))
        conn.commit()
    return cur.rowcount > 0


def _row_to_run(row) -> dict:
    def _parse(value, fallback):
        try:
            return json.loads(value) if value else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        "id": row["id"],
        "monitor_id": row["monitor_id"],
        "run_at": row["run_at"],
        "status": row["status"],
        "baseline": bool(row["baseline"]),
        "total_findings": row["total_findings"],
        "new_findings": row["new_findings"],
        "resolved_findings": row["resolved_findings"],
        "by_severity": _parse(row["by_severity"], {}),
        "new_fingerprints": _parse(row["new_fingerprints"], []),
        "resolved_fingerprints": _parse(row["resolved_fingerprints"], []),
        "visual_change_pct": row["visual_change_pct"],
        "has_screenshot": bool(row["screenshot_b64"]),
        "error": row["error"],
    }


def list_runs(monitor_id: int, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_monitor_runs WHERE monitor_id = ? "
            "ORDER BY run_at DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def get_run_screenshot(monitor_id: int, run_id: int) -> Optional[str]:
    """Base64 screenshot for one run, scoped to its monitor.

    Returns None when the run doesn't exist, belongs to a different
    monitor, or stored no screenshot. The raw base64 is returned —
    decoding/validation is the caller's job.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT screenshot_b64 FROM audit_monitor_runs "
            "WHERE id = ? AND monitor_id = ?",
            (run_id, monitor_id),
        ).fetchone()
    if row is None:
        return None
    return row["screenshot_b64"] or None


def get_run_diff_pair(
    monitor_id: int, run_id: int,
) -> Optional[tuple[str, str]]:
    """(previous, current) screenshots for rendering a run's visual diff.

    Previous is the newest successful run with a screenshot strictly older
    than this run (ties broken by id, so rapid consecutive runs work).
    Returns None when the run is unknown, belongs to another monitor, has
    no screenshot, or nothing earlier exists to compare against.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT run_at, screenshot_b64 FROM audit_monitor_runs "
            "WHERE id = ? AND monitor_id = ?",
            (run_id, monitor_id),
        ).fetchone()
        if row is None or not row["screenshot_b64"]:
            return None
        prev = conn.execute(
            "SELECT screenshot_b64 FROM audit_monitor_runs "
            "WHERE monitor_id = ? AND status = 'ok' "
            "AND screenshot_b64 IS NOT NULL AND screenshot_b64 != '' "
            "AND (run_at < ? OR (run_at = ? AND id < ?)) "
            "ORDER BY run_at DESC, id DESC LIMIT 1",
            (monitor_id, row["run_at"], row["run_at"], run_id),
        ).fetchone()
    if prev is None or not prev["screenshot_b64"]:
        return None
    return prev["screenshot_b64"], row["screenshot_b64"]


def _latest_successful_run(monitor_id: int) -> tuple[Optional[set[str]], Optional[str]]:
    """(fingerprints, screenshot) from the last successful run.

    Both are None when there is no successful prior run (baseline).
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT fingerprints, screenshot_b64 FROM audit_monitor_runs "
            "WHERE monitor_id = ? AND status = 'ok' ORDER BY run_at DESC LIMIT 1",
            (monitor_id,),
        ).fetchone()
    if row is None:
        return None, None
    try:
        fingerprints = set(json.loads(row["fingerprints"] or "[]"))
    except (TypeError, ValueError):
        fingerprints = set()
    return fingerprints, row["screenshot_b64"]


# ---------------------------------------------------------------------------
# Pure diff / alert logic (unit-testable without a DB or network)
# ---------------------------------------------------------------------------

def compute_diff(current_findings: list[dict], previous: Optional[set[str]]) -> dict:
    """Diff this run's findings against the previous run's fingerprints.

    ``previous=None`` means there is no successful prior run (baseline).
    """
    from backend.employees.export import content_fingerprint

    current: dict[str, dict] = {}
    for f in current_findings:
        current[content_fingerprint(f)] = f

    prev = previous or set()
    new = [f for fp, f in current.items() if fp not in prev]
    resolved = sorted(fp for fp in prev if fp not in current)
    persisting = len(current) - len(new)
    return {
        "new": new,
        "resolved": resolved,
        "persisting": persisting,
        "current_fingerprints": sorted(current.keys()),
        "baseline": previous is None,
    }


def alertable_findings(new_findings: list[dict], fail_on: str) -> list[dict]:
    """New findings at or above the monitor's alert threshold."""
    if fail_on == "none" or fail_on not in SEVERITIES:
        return []
    allowed = set(SEVERITIES[: SEVERITIES.index(fail_on) + 1])
    return [
        f for f in new_findings
        if str(f.get("severity", "")).lower() in allowed
    ]


def is_due(monitor: dict, now: Optional[float] = None) -> bool:
    """True when an enabled monitor is due for another run."""
    if not monitor.get("enabled", True):
        return False
    last = monitor.get("last_run_at")
    if not last:
        return True
    return (now or time.time()) >= last + monitor["interval_minutes"] * 60


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

async def send_webhook(webhook_url: str, payload: dict) -> bool:
    """POST a JSON payload to a user-configured webhook (SSRF-validated)."""
    from backend.core.security import is_safe_url

    if not is_safe_url(webhook_url):
        log.warning("Refusing webhook to unsafe URL: %s", webhook_url)
        return False
    try:
        from backend.core.socks import make_async_client
    except ImportError:  # pragma: no cover
        import httpx
        make_async_client = httpx.AsyncClient

    try:
        async with make_async_client(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
        if resp.status_code >= 300:
            log.warning("Webhook %s returned HTTP %s", webhook_url, resp.status_code)
            return False
        return True
    except Exception as e:
        log.warning("Webhook %s failed: %s", webhook_url, e)
        return False


async def _notify_alert(monitor: dict, new_findings: list[dict], resolved_count: int) -> None:
    """Desktop notification + optional webhook for a findings regression."""
    from backend.modules.notifications import Urgency, get_notifier

    titles = "; ".join(f.get("title", "?") for f in new_findings[:3])
    more = f" (+{len(new_findings) - 3} more)" if len(new_findings) > 3 else ""
    message = (
        f"{len(new_findings)} new finding(s) at/above "
        f"'{monitor['fail_on']}': {titles}{more}"
    )
    if resolved_count:
        message += f" — {resolved_count} resolved"
    try:
        await get_notifier().send(
            title=f"Audit regression: {monitor['url']}",
            message=message,
            urgency=Urgency.HIGH,
            category="audit_monitor",
        )
    except Exception:
        log.warning("Audit monitor desktop notification failed", exc_info=True)

    if monitor.get("webhook_url"):
        payload = {
            "event": "audit.regression",
            "monitor_id": monitor["id"],
            "url": monitor["url"],
            "fail_on": monitor["fail_on"],
            "new_findings": [
                {
                    "severity": f.get("severity"),
                    "employee": f.get("employee"),
                    "category": f.get("category"),
                    "title": f.get("title"),
                    "description": f.get("description"),
                    "fix_suggestion": f.get("fix_suggestion"),
                }
                for f in new_findings
            ],
            "resolved_count": resolved_count,
            "run_at": time.time(),
        }
        await send_webhook(monitor["webhook_url"], payload)


async def _notify_visual(monitor: dict, change_pct: float) -> None:
    """Desktop notification + optional webhook for a visual regression."""
    from backend.modules.notifications import Urgency, get_notifier

    threshold = monitor.get("visual_threshold_pct")
    try:
        await get_notifier().send(
            title=f"Visual change: {monitor['url']}",
            message=(
                f"{change_pct:.1f}% of pixels changed since the last run "
                f"(threshold {threshold}%)."
            ),
            urgency=Urgency.NORMAL,
            category="audit_monitor",
        )
    except Exception:
        log.warning("Visual alert notification failed", exc_info=True)

    if monitor.get("webhook_url"):
        payload = {
            "event": "audit.visual_change",
            "monitor_id": monitor["id"],
            "url": monitor["url"],
            "visual_change_pct": change_pct,
            "visual_threshold_pct": threshold,
            "run_at": time.time(),
        }
        await send_webhook(monitor["webhook_url"], payload)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

async def _execute_audit(url: str, mode: str) -> dict:
    """Run the shared audit pipeline and return its `done` payload.

    The captured screenshot (when available) is attached under
    ``_screenshot_base64`` for visual-regression diffing. Callers that
    don't need it simply ignore the key; it is never sent over SSE.
    """
    from backend.routes.audit import AuditRunRequest, _audit_event_stream

    captured: dict = {}

    def on_collected(audit_data) -> None:
        captured["screenshot"] = getattr(audit_data, "screenshot_base64", None)

    req = AuditRunRequest(url=url, mode=mode)
    done: Optional[dict] = None
    error_detail: Optional[dict] = None
    async for event, data in _audit_event_stream(req, on_collected=on_collected):
        if event == "done":
            done = data
        elif event == "error":
            error_detail = data
    if done is None:
        # Surface *why* the audit failed (missing Playwright browser,
        # navigation timeout, ...) instead of a useless generic message.
        detail = ""
        if error_detail:
            detail = (
                f" ({error_detail.get('phase', '?')}: "
                f"{error_detail.get('error', '?')})"
            )
        raise RuntimeError(f"audit did not produce a done event{detail}")
    done["_screenshot_base64"] = captured.get("screenshot")
    return done


def _persist_run(
    monitor_id: int,
    *,
    status: str,
    baseline: bool = False,
    total: int = 0,
    new: int = 0,
    resolved: int = 0,
    by_severity: Optional[dict] = None,
    fingerprints: Optional[list[str]] = None,
    new_fingerprints: Optional[list[str]] = None,
    resolved_fingerprints: Optional[list[str]] = None,
    screenshot_b64: Optional[str] = None,
    visual_change_pct: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO audit_monitor_runs
                (monitor_id, run_at, status, baseline, total_findings,
                 new_findings, resolved_findings, by_severity, fingerprints,
                 new_fingerprints, resolved_fingerprints, screenshot_b64,
                 visual_change_pct, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id, time.time(), status, 1 if baseline else 0, total, new,
                resolved,
                json.dumps(by_severity or {}),
                json.dumps(fingerprints or []),
                json.dumps(new_fingerprints or []),
                json.dumps(resolved_fingerprints or []),
                screenshot_b64,
                visual_change_pct,
                error,
            ),
        )
        conn.commit()
    _prune_runs(monitor_id)


def _prune_runs(monitor_id: int, keep: int = RUN_HISTORY_KEEP) -> None:
    """Keep only the newest ``keep`` runs per monitor.

    Screenshots are stored per run for visual diffing, so unbounded
    history would grow the database quickly.
    """
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM audit_monitor_runs WHERE monitor_id = ? AND id NOT IN ("
                "  SELECT id FROM audit_monitor_runs WHERE monitor_id = ?"
                "  ORDER BY run_at DESC, id DESC LIMIT ?)",
                (monitor_id, monitor_id, keep),
            )
            conn.commit()
    except Exception:
        log.warning("Failed to prune monitor runs", exc_info=True)


def _update_monitor_after_run(
    monitor_id: int, *, status: str, finding_count: Optional[int], error: Optional[str],
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE audit_monitors
            SET last_run_at = ?, last_status = ?, last_finding_count = ?, last_error = ?
            WHERE id = ?
            """,
            (time.time(), status, finding_count, error, monitor_id),
        )
        conn.commit()


async def run_monitor(monitor_id: int) -> dict:
    """Execute one monitor run: audit, diff, persist, alert.

    The first-ever run is a baseline: findings are recorded but no alert is
    sent (otherwise every monitor would fire on setup).
    """
    monitor = get_monitor(monitor_id)
    if monitor is None:
        raise ValueError(f"Monitor not found: {monitor_id}")

    previous, previous_screenshot = _latest_successful_run(monitor_id)
    started = time.time()

    try:
        done = await _execute_audit(monitor["url"], monitor["mode"])
    except Exception as e:
        log.warning("Audit monitor %s run failed: %s", monitor_id, e)
        _persist_run(monitor_id, status="error", error=str(e))
        _update_monitor_after_run(monitor_id, status="error", finding_count=None, error=str(e))
        return {"status": "error", "monitor_id": monitor_id, "error": str(e)}

    findings = done.get("findings", [])
    diff = compute_diff(findings, previous)
    new_alertable = alertable_findings(diff["new"], monitor["fail_on"])

    # Visual regression: compare this run's screenshot with the previous
    # successful run's (both must exist; decoding is skipped otherwise).
    from backend.modules.visual_diff import compute_visual_change

    current_screenshot = done.get("_screenshot_base64")
    visual_change_pct = compute_visual_change(previous_screenshot, current_screenshot)
    threshold = monitor.get("visual_threshold_pct")
    visual_changed = bool(
        visual_change_pct is not None
        and threshold is not None
        and threshold > 0
        and visual_change_pct >= threshold
        and not diff["baseline"]
    )

    from backend.employees.export import content_fingerprint
    new_fps = sorted(content_fingerprint(f) for f in diff["new"])
    _persist_run(
        monitor_id,
        status="ok",
        baseline=diff["baseline"],
        total=len(findings),
        new=len(diff["new"]),
        resolved=len(diff["resolved"]),
        by_severity=done.get("by_severity"),
        fingerprints=diff["current_fingerprints"],
        new_fingerprints=new_fps,
        resolved_fingerprints=diff["resolved"],
        screenshot_b64=current_screenshot,
        visual_change_pct=visual_change_pct,
    )
    _update_monitor_after_run(
        monitor_id, status="ok", finding_count=len(findings), error=None,
    )

    alerted = False
    if new_alertable and not diff["baseline"]:
        await _notify_alert(monitor, new_alertable, len(diff["resolved"]))
        alerted = True

    visual_alerted = False
    if visual_changed:
        await _notify_visual(monitor, visual_change_pct)
        visual_alerted = True

    return {
        "status": "ok",
        "monitor_id": monitor_id,
        "url": monitor["url"],
        "baseline": diff["baseline"],
        "total_findings": len(findings),
        "new_findings": len(diff["new"]),
        "resolved_findings": len(diff["resolved"]),
        "alerted": alerted,
        "alert_findings": new_alertable if alerted else [],
        "visual_change_pct": visual_change_pct,
        "visual_changed": visual_changed,
        "visual_alerted": visual_alerted,
        "duration_ms": round((time.time() - started) * 1000),
    }


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class AuditMonitorScheduler:
    """Background loop that runs due monitors on a fixed tick."""

    def __init__(self, check_interval: int = DEFAULT_CHECK_INTERVAL, initial_delay: int = 10):
        self._check_interval = check_interval
        self._initial_delay = initial_delay
        self._running = False

    async def check_due_monitors(self) -> list[dict]:
        """Run every enabled monitor whose interval has elapsed."""
        due = [m for m in list_monitors() if is_due(m)]
        results = []
        for monitor in due:
            try:
                results.append(await run_monitor(monitor["id"]))
            except Exception as e:  # pragma: no cover — defensive
                log.error("Monitor %s crashed: %s", monitor["id"], e)
        return results

    async def run_loop(self) -> None:
        self._running = True
        # Let engine startup finish before touching Playwright/LLM.
        await asyncio.sleep(self._initial_delay)
        while self._running:
            try:
                ran = await self.check_due_monitors()
                if ran:
                    log.info("Audit monitor scheduler ran %d monitor(s)", len(ran))
            except Exception as e:
                log.error("Audit monitor scheduler error: %s", e)
            await asyncio.sleep(self._check_interval)

    def stop(self) -> None:
        self._running = False


_scheduler: Optional[AuditMonitorScheduler] = None


def get_monitor_scheduler() -> AuditMonitorScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AuditMonitorScheduler()
    return _scheduler
