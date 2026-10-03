"""Plan library: inspect and prune the cached plan templates.

The library itself (match/advise) is wired inside the agent loop; these
routes are the operator's view — what has been cached and its rolling
success rate, plus deletion so a bad template does not linger.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["agent"])


@router.get("/agent/plan-library")
async def plan_library(limit: int = Query(20, ge=1, le=200)):
    """Top cached plan templates by success rate."""
    from backend.agent.plan_library import get_plan_library

    lib = get_plan_library()
    return {"count": len(lib), "entries": lib.top(limit)}


@router.post("/agent/plan-library/clear")
async def plan_library_clear():
    from backend.agent.plan_library import get_plan_library

    removed = get_plan_library().clear()
    return {"cleared": removed}


@router.delete("/agent/plan-library")
async def plan_library_remove(query: str = Query(..., min_length=1)):
    from backend.agent.plan_library import get_plan_library

    if not get_plan_library().remove(query):
        raise HTTPException(status_code=404, detail="no cached plan for that goal")
    return {"removed": True}
