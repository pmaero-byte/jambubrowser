"""Recurring audit monitors with regression alerting.

A monitor schedules the audit pipeline on an interval and diffs the active
findings against the previous run. Alerts (desktop notification + optional
webhook) fire only for *new* findings at or above the monitor's `fail_on`
severity. The first run is a baseline and never alerts.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, validator

from backend.core.security import is_safe_url
from backend.modules import audit_monitor as monitor_store

router = APIRouter(prefix="/audit", tags=["audit-monitors"])

MODE_CHOICES = ("quick", "full")
FAIL_ON_CHOICES = ("critical", "high", "medium", "low", "none")


def _validate_url(value: str) -> str:
    if not is_safe_url(value):
        raise ValueError("Invalid or blocked URL")
    return value


def _validate_mode(value: str) -> str:
    if value not in MODE_CHOICES:
        raise ValueError(f"mode must be one of {MODE_CHOICES}")
    return value


def _validate_fail_on(value: str) -> str:
    if value not in FAIL_ON_CHOICES:
        raise ValueError(f"fail_on must be one of {FAIL_ON_CHOICES}")
    return value


def _validate_interval(value: int) -> int:
    if value < monitor_store.MIN_INTERVAL_MINUTES:
        raise ValueError(
            f"interval_minutes must be >= {monitor_store.MIN_INTERVAL_MINUTES}"
        )
    return value


def _validate_webhook(value: Optional[str]) -> Optional[str]:
    if value and not is_safe_url(value):
        raise ValueError("webhook_url must be a public http(s) URL")
    return value


def _validate_visual_threshold(value: float) -> float:
    if value < 0:
        raise ValueError(
            "visual_threshold_pct must be >= 0 (0 disables visual alerts)"
        )
    return value


class MonitorCreateRequest(BaseModel):
    url: str
    mode: str = "quick"
    interval_minutes: int = monitor_store.DEFAULT_INTERVAL_MINUTES
    fail_on: str = "high"
    webhook_url: Optional[str] = None
    enabled: bool = True
    run_now: bool = False
    visual_threshold_pct: float = monitor_store.DEFAULT_VISUAL_THRESHOLD_PCT

    @validator("url")
    def validate_url(cls, v):
        return _validate_url(v)

    @validator("mode")
    def validate_mode(cls, v):
        return _validate_mode(v)

    @validator("interval_minutes")
    def validate_interval(cls, v):
        return _validate_interval(v)

    @validator("fail_on")
    def validate_fail_on(cls, v):
        return _validate_fail_on(v)

    @validator("webhook_url")
    def validate_webhook(cls, v):
        return _validate_webhook(v)

    @validator("visual_threshold_pct")
    def validate_visual_threshold(cls, v):
        return _validate_visual_threshold(v)


class MonitorUpdateRequest(BaseModel):
    url: Optional[str] = None
    mode: Optional[str] = None
    interval_minutes: Optional[int] = None
    fail_on: Optional[str] = None
    webhook_url: Optional[str] = None
    enabled: Optional[bool] = None
    visual_threshold_pct: Optional[float] = None

    @validator("url")
    def validate_url(cls, v):
        return _validate_url(v) if v is not None else v

    @validator("mode")
    def validate_mode(cls, v):
        return _validate_mode(v) if v is not None else v

    @validator("interval_minutes")
    def validate_interval(cls, v):
        return _validate_interval(v) if v is not None else v

    @validator("fail_on")
    def validate_fail_on(cls, v):
        return _validate_fail_on(v) if v is not None else v

    @validator("webhook_url")
    def validate_webhook(cls, v):
        return _validate_webhook(v)

    @validator("visual_threshold_pct")
    def validate_visual_threshold(cls, v):
        return _validate_visual_threshold(v) if v is not None else v


@router.post("/monitors")
async def create_audit_monitor(req: MonitorCreateRequest):
    """Create a recurring audit monitor.

    With ``run_now: true`` the first audit executes inline and its diff
    summary is returned as ``initial_run`` (baseline — no alert).
    """
    monitor = monitor_store.create_monitor(
        url=req.url,
        mode=req.mode,
        interval_minutes=req.interval_minutes,
        fail_on=req.fail_on,
        webhook_url=req.webhook_url,
        enabled=req.enabled,
        visual_threshold_pct=req.visual_threshold_pct,
    )
    initial_run = None
    if req.run_now:
        initial_run = await monitor_store.run_monitor(monitor["id"])
        monitor = monitor_store.get_monitor(monitor["id"])
    return {"monitor": monitor, "initial_run": initial_run}


@router.get("/monitors")
async def list_audit_monitors():
    """List all audit monitors."""
    monitors = monitor_store.list_monitors()
    return {"monitors": monitors, "count": len(monitors)}


@router.post("/monitors/check-now")
async def check_audit_monitors_now():
    """Run every enabled monitor whose interval has elapsed (ops/testing hook)."""
    results = await monitor_store.get_monitor_scheduler().check_due_monitors()
    return {"ran": results, "count": len(results)}


@router.get("/monitors/{monitor_id}")
async def get_audit_monitor(monitor_id: int):
    monitor = monitor_store.get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return {"monitor": monitor}


@router.patch("/monitors/{monitor_id}")
async def update_audit_monitor(monitor_id: int, req: MonitorUpdateRequest):
    """Update a monitor (interval, threshold, webhook, enabled, ...)."""
    fields = {k: v for k, v in req.dict(exclude_unset=True).items() if v is not None}
    monitor = monitor_store.update_monitor(monitor_id, **fields)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return {"monitor": monitor}


@router.delete("/monitors/{monitor_id}")
async def delete_audit_monitor(monitor_id: int):
    """Delete a monitor and its run history."""
    if not monitor_store.delete_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="Monitor not found")
    return {"status": "deleted", "monitor_id": monitor_id}


@router.post("/monitors/{monitor_id}/run")
async def run_audit_monitor(monitor_id: int):
    """Run a monitor immediately (audit + diff + alert)."""
    if monitor_store.get_monitor(monitor_id) is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return await monitor_store.run_monitor(monitor_id)


@router.get("/monitors/{monitor_id}/runs")
async def list_audit_monitor_runs(monitor_id: int, limit: int = 20):
    """Run history for a monitor, newest first, with per-run diffs."""
    if monitor_store.get_monitor(monitor_id) is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    runs = monitor_store.list_runs(monitor_id, limit=limit)
    return {"monitor_id": monitor_id, "runs": runs, "count": len(runs)}


@router.get("/monitors/{monitor_id}/runs/{run_id}/screenshot")
async def get_audit_monitor_run_screenshot(monitor_id: int, run_id: int):
    """PNG screenshot captured during one monitor run.

    Screenshots are stored per run for visual diffing; without this route
    they are write-only. 404 when the monitor/run is unknown, belongs to a
    different monitor, or stored no screenshot; 500 when the stored payload
    is not decodable image data.
    """
    import base64

    if monitor_store.get_monitor(monitor_id) is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    screenshot_b64 = monitor_store.get_run_screenshot(monitor_id, run_id)
    if not screenshot_b64:
        raise HTTPException(status_code=404, detail="No screenshot for this run")
    try:
        png_bytes = base64.b64decode(screenshot_b64, validate=True)
    except Exception:
        raise HTTPException(
            status_code=500, detail="Stored screenshot is not valid image data",
        )
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(
            status_code=500, detail="Stored screenshot is not valid image data",
        )
    return Response(content=png_bytes, media_type="image/png")


@router.get("/monitors/{monitor_id}/runs/{run_id}/diff")
async def get_audit_monitor_run_diff(monitor_id: int, run_id: int):
    """PNG heatmap of what changed since the previous run with a screenshot.

    Unchanged pixels are dimmed, changed pixels are red (same tolerance
    rule as the change percentage). 404 when the run is unknown, has no
    screenshot, or nothing earlier exists to compare against; 500 when the
    diff can't be rendered.
    """
    import base64

    from backend.modules.visual_diff import render_diff_image

    if monitor_store.get_monitor(monitor_id) is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    pair = monitor_store.get_run_diff_pair(monitor_id, run_id)
    if pair is None:
        raise HTTPException(
            status_code=404, detail="No diff available for this run",
        )
    diff_b64 = render_diff_image(pair[0], pair[1])
    if diff_b64 is None:
        raise HTTPException(
            status_code=500, detail="Could not render diff image",
        )
    return Response(
        content=base64.b64decode(diff_b64), media_type="image/png",
    )
