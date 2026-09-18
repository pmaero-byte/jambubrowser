"""
Flow-monitor routes — recurring agent test flows.

A flow monitor stores a declarative step flow and re-runs it on an interval,
persisting each run and alerting on regression. Distinct from audit monitors
(which diff findings/visuals for a URL); this drives the agent test runner.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.modules import flow_monitor

router = APIRouter(prefix="/browser/monitors", tags=["flow-monitors"])


class MonitorCreate(BaseModel):
    name: str
    url: str
    steps: list[dict]
    local: bool = True
    approve: bool = False
    network: Optional[dict] = None
    interval_minutes: int = 1440
    webhook_url: Optional[str] = None
    enabled: bool = True

    @validator("steps")
    def validate_steps(cls, v):
        if not v:
            raise ValueError("steps must be non-empty")
        return v


class MonitorUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    steps: Optional[list[dict]] = None
    local: Optional[bool] = None
    approve: Optional[bool] = None
    network: Optional[dict] = None
    interval_minutes: Optional[int] = None
    webhook_url: Optional[str] = None
    enabled: Optional[bool] = None


@router.post("")
async def create_monitor(req: MonitorCreate):
    return flow_monitor.create_monitor(
        req.name, req.url, req.steps, local=req.local, approve=req.approve,
        network=req.network, interval_minutes=req.interval_minutes,
        webhook_url=req.webhook_url, enabled=req.enabled,
    )


@router.get("")
async def list_monitors():
    monitors = flow_monitor.list_monitors()
    return {"monitors": monitors, "count": len(monitors)}


@router.get("/{monitor_id}")
async def get_monitor(monitor_id: int):
    monitor = flow_monitor.get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="flow monitor not found")
    return monitor


@router.patch("/{monitor_id}")
async def update_monitor(monitor_id: int, req: MonitorUpdate):
    fields = req.dict(exclude_none=True)
    monitor = flow_monitor.update_monitor(monitor_id, **fields)
    if monitor is None:
        raise HTTPException(status_code=404, detail="flow monitor not found")
    return monitor


@router.delete("/{monitor_id}")
async def delete_monitor(monitor_id: int):
    if not flow_monitor.delete_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="flow monitor not found")
    return {"monitor_id": monitor_id, "deleted": True}


@router.post("/{monitor_id}/run")
async def run_monitor(monitor_id: int):
    try:
        return await flow_monitor.run_monitor(monitor_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{monitor_id}/runs")
async def list_runs(monitor_id: int, limit: int = 20):
    if flow_monitor.get_monitor(monitor_id) is None:
        raise HTTPException(status_code=404, detail="flow monitor not found")
    runs = flow_monitor.list_runs(monitor_id, limit=limit)
    return {"monitor_id": monitor_id, "runs": runs, "count": len(runs)}
