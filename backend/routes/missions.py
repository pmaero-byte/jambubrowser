"""Mission, shadow browser, shield, and notification endpoints."""
import time
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.core.database import get_db_cursor
from backend.core.security import is_safe_url

router = APIRouter(tags=["missions"])


# ── Pydantic Models ──


class MissionRequest(BaseModel):
    query: str


class MissionStopRequest(BaseModel):
    mission_id: str


class MissionScheduleRequest(BaseModel):
    query: str
    schedule: Optional[str] = None
    priority: int = 1
    trigger_conditions: Optional[dict] = None


class ShieldRequest(BaseModel):
    url: str
    real_time: bool = False

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class ShieldBatchRequest(BaseModel):
    urls: List[str]

    @validator("urls")
    def validate_urls(cls, v):
        for url in v:
            if not is_safe_url(url):
                raise ValueError(f"Invalid or blocked URL: {url}")
        return v


class ShadowInterestRequest(BaseModel):
    interests: List[str]


class NotificationSendRequest(BaseModel):
    title: str
    message: str
    level: str = "info"


# ── Missions ──


@router.post("/mission")
async def start_mission(req: MissionRequest):
    """Register a background research mission."""
    import hashlib
    mid = hashlib.md5(req.query.encode()).hexdigest()[:8]
    with get_db_cursor() as cursor:
        cursor.execute(
            "INSERT OR REPLACE INTO missions (id, query, status, last_run, next_run, schedule) VALUES (?, ?, 'active', ?, ?, 'none')",
            (mid, req.query, time.time(), 0),
        )
    return {"mission_id": mid, "status": "active"}


@router.post("/mission/stop")
async def stop_mission(req: MissionStopRequest):
    """Stop a background research mission."""
    with get_db_cursor() as cursor:
        cursor.execute("UPDATE missions SET status = 'stopped' WHERE id = ?", (req.mission_id,))
    return {"mission_id": req.mission_id, "status": "stopped"}


@router.post("/mission/schedule")
async def schedule_mission(req: MissionScheduleRequest):
    """Schedule an advanced mission with cron expression."""
    try:
        from backend.modules.missions import get_scheduler, parse_cron, get_next_run
        scheduler = get_scheduler()
        mission = await scheduler.add_mission(
            query=req.query, schedule=req.schedule, priority=req.priority,
            trigger_conditions=req.trigger_conditions,
        )
        next_run = get_next_run(req.schedule) if req.schedule else None
        return {
            "mission_id": mission.id, "status": mission.status,
            "query": mission.query, "schedule": mission.schedule,
            "priority": mission.priority, "next_run": next_run,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mission/list")
async def list_missions(status: str = None):
    """List all scheduled missions with status and run history."""
    try:
        from backend.modules.missions import get_scheduler
        missions = get_scheduler().list_missions(status=status)
        return {"missions": missions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mission/{mission_id}/results")
async def mission_results(mission_id: str, limit: int = 50):
    """Return the most recent collected results for a mission, newest first."""
    from backend.core.database import get_db_cursor
    with get_db_cursor() as cursor:
        cursor.execute("SELECT id FROM missions WHERE id = ?", (mission_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Mission not found: {mission_id}")
    try:
        from backend.modules.missions import get_scheduler
        results = get_scheduler().get_results(mission_id, limit=limit)
        return {"mission_id": mission_id, "results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mission/start-scheduler")
async def start_mission_scheduler():
    """Start the background mission scheduler loop."""
    try:
        from backend.modules.missions import get_scheduler
        get_scheduler().start()
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mission/stop-scheduler")
async def stop_mission_scheduler():
    """Stop the background mission scheduler loop."""
    try:
        from backend.modules.missions import get_scheduler
        get_scheduler().stop()
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Shield (Risk Shield) ──


@router.post("/shield/check")
async def shield_check(req: ShieldRequest):
    """Assess the risk of a URL using all available sources."""
    if not req.url or not req.url.startswith(("http://", "https://")):
        return {"url": req.url, "risk_level": "invalid", "blocked": True,
                "consensus_score": 1.0, "reason": "Invalid or empty URL",
                "checks": [{"source": "heuristic", "risk_level": "invalid", "score": 1.0,
                           "details": "URL is empty or has invalid scheme"}]}
    try:
        from backend.modules.risk_shield import get_shield
        result = await get_shield().assess_url(req.url, real_time=req.real_time)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/shield/batch")
async def shield_batch(req: ShieldBatchRequest):
    """Batch risk assessment for multiple URLs."""
    try:
        from backend.modules.risk_shield import get_shield
        results = await get_shield().batch_assess(req.urls)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shield/stats")
async def shield_stats():
    """Get risk shield cache statistics."""
    from backend.modules.risk_shield import get_shield
    return get_shield().get_cache_stats()


# ── Shadow Browser ──


@router.post("/shadow/start")
async def start_shadow_browser():
    """Start the autonomous shadow browser background loop."""
    try:
        from backend.modules.shadow_browser import get_shadow_browser
        await get_shadow_browser().start()
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shadow/stats")
async def shadow_browser_stats():
    """Get shadow browser statistics."""
    from backend.modules.shadow_browser import get_shadow_browser
    return get_shadow_browser().get_stats()


@router.get("/shadow/interests")
async def shadow_interests():
    """List interest profiles for the shadow browser."""
    from backend.modules.shadow_browser import get_shadow_browser
    return {"interests": get_shadow_browser().get_interests()}


@router.post("/shadow/interests")
async def add_shadow_interest(req: ShadowInterestRequest):
    """Set interest topics for the shadow browser."""
    from backend.modules.shadow_browser import get_shadow_browser
    get_shadow_browser().set_interests(req.interests)
    return {"status": "set", "interests": req.interests}


@router.delete("/shadow/interests/{name}")
async def remove_shadow_interest(name: str):
    """Remove a shadow browser interest."""
    from backend.modules.shadow_browser import get_shadow_browser
    get_shadow_browser().remove_interest(name)
    return {"status": "removed", "interest": name}


@router.post("/shadow/stop")
async def stop_shadow_browser():
    """Stop the shadow browser background loop."""
    try:
        from backend.modules.shadow_browser import get_shadow_browser
        await get_shadow_browser().stop()
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Notifications ──


@router.get("/notifications/history")
async def notification_history(category: str = None, limit: int = 20):
    """Get notification history."""
    try:
        from backend.modules.notifications import get_notification_manager
        mgr = get_notification_manager()
        entries = mgr.get_history(category=category, limit=limit)
        return {"entries": entries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notifications/send")
async def send_notification_endpoint(req: NotificationSendRequest):
    """Send a test/system notification."""
    try:
        from backend.modules.notifications import get_notification_manager
        mgr = get_notification_manager()
        await mgr.send(req.title, req.message, level=req.level)
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
