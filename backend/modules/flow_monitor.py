"""
Recurring agent test flows ("flow monitors").

A flow monitor stores a declarative step flow and re-runs it on an interval
through :class:`BrowserAgentService`. Each run is persisted; a failure alerts
via desktop notification and an optional webhook. This turns one debugging
session into permanent regression protection.

Reuses the audit monitor's webhook transport and due-checking so behaviour
(SSRF-validated webhooks, interval semantics) is identical.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from backend.core.database import get_db
from backend.modules.audit_monitor import is_due, send_webhook

log = logging.getLogger("jambu.flow_monitor")

DEFAULT_INTERVAL_MINUTES = 1440
MIN_INTERVAL_MINUTES = 5
RUN_HISTORY_KEEP = 50


def _row_to_monitor(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "steps": json.loads(row["steps"] or "[]"),
        "local": bool(row["local"]),
        "approve": bool(row["approve"]),
        "network": json.loads(row["network"]) if row["network"] else None,
        "interval_minutes": row["interval_minutes"],
        "webhook_url": row["webhook_url"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"],
    }


def _row_to_run(row) -> dict:
    def _load(value):
        try:
            return json.loads(value) if value else None
        except (TypeError, ValueError):
            return None

    return {
        "id": row["id"],
        "monitor_id": row["monitor_id"],
        "run_at": row["run_at"],
        "status": row["status"],
        "ok": bool(row["ok"]),
        "passed": row["passed"],
        "failed": row["failed"],
        "total": row["total"],
        "duration_ms": row["duration_ms"],
        "failed_steps": _load(row["failed_steps"]) or [],
        "console_errors": _load(row["console_errors"]) or [],
        "artifacts": _load(row["artifacts"]) or {},
        "error": row["error"],
    }


def create_monitor(name: str, url: str, steps: list, *,
                   local: bool = True, approve: bool = False,
                   network: Optional[dict] = None,
                   interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
                   webhook_url: Optional[str] = None,
                   enabled: bool = True) -> dict:
    interval = max(MIN_INTERVAL_MINUTES, int(interval_minutes))
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO flow_monitors
                (name, url, steps, local, approve, network, interval_minutes,
                 webhook_url, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, url, json.dumps(steps or []), 1 if local else 0,
                1 if approve else 0, json.dumps(network) if network else None,
                interval, webhook_url, 1 if enabled else 0,
            ),
        )
        conn.commit()
        monitor_id = cur.lastrowid
    return get_monitor(monitor_id)


def get_monitor(monitor_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM flow_monitors WHERE id = ?", (monitor_id,)
        ).fetchone()
    return _row_to_monitor(row) if row else None


def list_monitors() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM flow_monitors ORDER BY id").fetchall()
    return [_row_to_monitor(r) for r in rows]


def update_monitor(monitor_id: int, **fields) -> Optional[dict]:
    allowed = {"name", "url", "steps", "local", "approve", "network",
               "interval_minutes", "webhook_url", "enabled"}
    updates: dict = {}
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "steps":
            value = json.dumps(value)
        elif key == "network":
            value = json.dumps(value)
        elif key in ("local", "approve", "enabled"):
            value = 1 if value else 0
        updates[key] = value
    if not updates:
        return get_monitor(monitor_id)
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with get_db() as conn:
        conn.execute(
            f"UPDATE flow_monitors SET {assignments} WHERE id = ?",
            (*updates.values(), monitor_id),
        )
        conn.commit()
    return get_monitor(monitor_id)


def delete_monitor(monitor_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM flow_monitor_runs WHERE monitor_id = ?", (monitor_id,))
        cur = conn.execute("DELETE FROM flow_monitors WHERE id = ?", (monitor_id,))
        conn.commit()
        return cur.rowcount > 0


def list_runs(monitor_id: int, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_monitor_runs WHERE monitor_id = ? "
            "ORDER BY run_at DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


async def run_monitor(monitor_id: int) -> dict:
    """Execute a flow monitor once and persist the result."""
    monitor = get_monitor(monitor_id)
    if monitor is None:
        raise ValueError(f"no such flow monitor: {monitor_id}")

    from backend.modules.browser_agent import BrowserAgentService

    status = "ok"
    error = None
    report: dict = {}
    try:
        service = BrowserAgentService()
        report = await service.run_test(
            url=monitor["url"], steps=monitor["steps"],
            local=monitor["local"], approve=monitor["approve"],
            network=monitor["network"], stop_on_failure=False,
        )
        status = "passed" if report.get("ok") else "failed"
    except Exception as exc:
        status = "error"
        error = str(exc)[:300]
        log.warning("flow monitor %s failed to run", monitor_id, exc_info=True)

    failed_steps = [
        {"i": s.get("i"), "action": s.get("action"),
         "reason": s.get("reason"), "error": s.get("error")}
        for s in report.get("steps") or [] if s.get("status") == "failed"
    ]
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO flow_monitor_runs
                (monitor_id, status, ok, passed, failed, total, duration_ms,
                 failed_steps, console_errors, artifacts, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id, status, 1 if report.get("ok") else 0,
                report.get("passed", 0), report.get("failed", 0),
                report.get("total", 0), report.get("duration_ms", 0),
                json.dumps(failed_steps),
                json.dumps(report.get("console_errors") or []),
                json.dumps(report.get("artifacts") or {}),
                error,
            ),
        )
        conn.execute(
            "UPDATE flow_monitors SET last_run_at = ?, last_status = ? WHERE id = ?",
            (time.time(), status, monitor_id),
        )
        conn.execute(
            "DELETE FROM flow_monitor_runs WHERE monitor_id = ? AND id NOT IN ("
            "SELECT id FROM flow_monitor_runs WHERE monitor_id = ? "
            "ORDER BY run_at DESC LIMIT ?)",
            (monitor_id, monitor_id, RUN_HISTORY_KEEP),
        )
        conn.commit()

    if status in ("failed", "error"):
        await _notify_failure(monitor, status, failed_steps, error)

    return {
        "monitor_id": monitor_id,
        "status": status,
        "ok": bool(report.get("ok")),
        "passed": report.get("passed", 0),
        "failed": report.get("failed", 0),
        "total": report.get("total", 0),
        "failed_steps": failed_steps[:5],
        "error": error,
    }


async def _notify_failure(monitor: dict, status: str, failed_steps: list,
                          error: Optional[str]) -> None:
    title = f"Flow monitor failed: {monitor['name']}"
    body = (
        f"{len(failed_steps)} step(s) failed on {monitor['url']}"
        if failed_steps else (error or "run error")
    )
    try:
        import subprocess
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification {json.dumps(body)} with title {json.dumps(title)}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("desktop notification unavailable", exc_info=True)
    if monitor.get("webhook_url"):
        await send_webhook(monitor["webhook_url"], {
            "event": "flow.regression",
            "monitor_id": monitor["id"],
            "name": monitor["name"],
            "url": monitor["url"],
            "status": status,
            "failed_steps": failed_steps[:5],
            "error": error,
            "run_at": time.time(),
        })


class FlowMonitorScheduler:
    """Background loop that runs due flow monitors."""

    def __init__(self, check_interval: int = 60, initial_delay: int = 15):
        self.check_interval = check_interval
        self.initial_delay = initial_delay
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def _loop(self) -> None:
        await asyncio.sleep(self.initial_delay)
        while self._running:
            try:
                for monitor in list_monitors():
                    if monitor["enabled"] and is_due(monitor):
                        await run_monitor(monitor["id"])
            except Exception:
                log.warning("flow monitor scheduler tick failed", exc_info=True)
            await asyncio.sleep(self.check_interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


_scheduler: Optional[FlowMonitorScheduler] = None


def get_flow_monitor_scheduler() -> FlowMonitorScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = FlowMonitorScheduler()
    return _scheduler
