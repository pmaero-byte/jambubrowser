"""Goal orchestrator endpoints."""
from typing import Optional, List
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["goals"])


class GoalSetRequest(BaseModel):
    title: str
    description: str
    success_criteria: List[str] = []
    constraints: List[str] = []
    priority: int = 3


class ApproachRecordRequest(BaseModel):
    goal_id: str = None
    strategy: str
    hypothesis: str = ""
    iteration: int = None


class ApproachUpdateRequest(BaseModel):
    approach_id: str
    result: str  # success, falsified, partial
    evidence: str = ""
    learning: str = ""
    next_target: str = ""


@router.post("/goal/set")
async def goal_set(req: GoalSetRequest):
    """Set the browser's sovereign goal."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    orch = get_goal_orchestrator()
    goal = orch.set_goal(req.title, req.description,
                          req.success_criteria, req.constraints, req.priority)
    return {"status": "goal_set", "goal": {
        "id": goal.id, "title": goal.title, "status": goal.status,
        "priority": goal.priority, "approaches_tried": goal.approaches_tried,
    }}


@router.get("/goal/active")
async def goal_active():
    """Get the currently active sovereign goal."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    orch = get_goal_orchestrator()
    goal = orch.get_active_goal()
    if not goal:
        return {"active": False, "message": "No active goal. Set one with POST /goal/set"}
    return {"active": True, "goal": {
        "id": goal.id, "title": goal.title, "description": goal.description,
        "status": goal.status, "priority": goal.priority,
        "approaches_tried": goal.approaches_tried,
        "approaches_succeeded": goal.approaches_succeeded,
        "success_criteria": goal.success_criteria,
        "constraints": goal.constraints,
    }}


@router.get("/goal/list")
async def goal_list(status: str = None):
    """List all goals, optionally filtered by status."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    goals = get_goal_orchestrator().list_goals(status)
    return {"goals": [
        {"id": g.id, "title": g.title, "status": g.status,
         "priority": g.priority, "approaches_tried": g.approaches_tried}
        for g in goals
    ]}


@router.post("/goal/achieve")
async def goal_achieve(goal_id: str = None):
    """Mark the active goal as achieved."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    success = get_goal_orchestrator().achieve_goal(goal_id)
    return {"status": "achieved" if success else "not_found"}


@router.post("/goal/block")
async def goal_block(goal_id: str = None, reason: str = ""):
    """Mark a goal as blocked."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    get_goal_orchestrator().block_goal(goal_id, reason)
    return {"status": "blocked"}


@router.post("/goal/approach")
async def goal_record_approach(req: ApproachRecordRequest):
    """Record a new approach attempt toward the active goal."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    orch = get_goal_orchestrator()
    approach = orch.record_approach(req.goal_id, req.strategy,
                                     req.hypothesis, req.iteration)
    return {"status": "recorded", "approach": {
        "id": approach.id, "iteration": approach.iteration,
        "strategy": approach.strategy[:100],
    }}


@router.post("/goal/approach/update")
async def goal_update_approach(req: ApproachUpdateRequest):
    """Update an approach with results and learning."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    success = get_goal_orchestrator().update_approach(
        req.approach_id, req.result, req.evidence, req.learning, req.next_target)
    return {"status": "updated" if success else "not_found"}


@router.get("/goal/approaches")
async def goal_approaches(goal_id: str = None, limit: int = 10):
    """Get approaches for a goal."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    approaches = get_goal_orchestrator().get_approaches(goal_id, limit)
    return {"approaches": approaches}


@router.get("/goal/fallback")
async def goal_fallback(goal_id: str = None):
    """Generate fallback strategies when a goal is blocked."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    return get_goal_orchestrator().generate_fallback(goal_id)


@router.post("/goal/inject")
async def goal_inject(user_query: str):
    """Preview the goal-injected prompt."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    return get_goal_orchestrator().inject_goal(user_query)


@router.get("/goal/context")
async def goal_context():
    """Get condensed goal context for LLM."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    return get_goal_orchestrator().get_context()


@router.get("/goal/learnings")
async def goal_learnings(query: str, limit: int = 10):
    """Query RAG for past iteration learnings."""
    from backend.modules.goal_orchestrator import get_goal_orchestrator
    return {"learnings": get_goal_orchestrator().query_learnings(query, limit)}
