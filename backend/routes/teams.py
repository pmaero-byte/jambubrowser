"""Team management routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.core.teams import (
    create_team,
    get_team,
    list_teams,
    add_member,
    list_members,
    remove_member,
    assign_finding,
    list_assignments,
    resolve_assignment,
    get_team_activity,
    get_team_stats,
)

router = APIRouter(prefix="/teams", tags=["teams"])


class CreateTeamRequest(BaseModel):
    name: str
    owner: str = "default"


class AddMemberRequest(BaseModel):
    email: str
    name: Optional[str] = None
    role: str = "member"


class AssignFindingRequest(BaseModel):
    audit_id: int
    finding_id: str
    assigned_to: str
    assigned_by: str = "default"
    notes: Optional[str] = None


class ResolveRequest(BaseModel):
    resolved_by: str = "default"


@router.post("/create")
async def teams_create(req: CreateTeamRequest):
    team = create_team(req.name, req.owner)
    return {
        "team": {
            "id": team.id,
            "name": team.name,
            "slug": team.slug,
            "owner": team.owner,
            "member_count": team.member_count,
        }
    }


@router.get("/list")
async def teams_list(owner: str = "default"):
    teams = list_teams(owner)
    return {
        "teams": [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "owner": t.owner,
                "plan": t.plan,
                "member_count": t.member_count,
            }
            for t in teams
        ]
    }


@router.get("/{team_id}")
async def teams_get(team_id: int):
    team = get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    stats = get_team_stats(team_id)
    return {
        "team": {
            "id": team.id,
            "name": team.name,
            "slug": team.slug,
            "owner": team.owner,
            "plan": team.plan,
            "member_count": team.member_count,
        },
        "stats": stats,
    }


@router.post("/{team_id}/members")
async def teams_add_member(team_id: int, req: AddMemberRequest):
    team = get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    member = add_member(team_id, req.email, req.name, req.role)
    return {
        "member": {
            "id": member.id,
            "email": member.email,
            "name": member.name,
            "role": member.role,
        }
    }


@router.get("/{team_id}/members")
async def teams_list_members(team_id: int):
    members = list_members(team_id)
    return {
        "members": [
            {
                "id": m.id,
                "email": m.email,
                "name": m.name,
                "role": m.role,
                "joined_at": m.joined_at,
            }
            for m in members
        ]
    }


@router.delete("/{team_id}/members/{email}")
async def teams_remove_member(team_id: int, email: str):
    if remove_member(team_id, email):
        return {"status": "removed", "email": email}
    raise HTTPException(status_code=404, detail="Member not found or is owner")


@router.post("/{team_id}/assignments")
async def teams_assign_finding(team_id: int, req: AssignFindingRequest):
    team = get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    assignment = assign_finding(
        req.audit_id, req.finding_id, team_id,
        req.assigned_to, req.assigned_by, req.notes,
    )
    return {
        "assignment": {
            "id": assignment.id,
            "finding_id": assignment.finding_id,
            "assigned_to": assignment.assigned_to,
            "status": assignment.status,
        }
    }


@router.get("/{team_id}/assignments")
async def teams_list_assignments(team_id: int, status: str = None):
    assignments = list_assignments(team_id, status)
    return {
        "assignments": [
            {
                "id": a.id,
                "audit_id": a.audit_id,
                "finding_id": a.finding_id,
                "assigned_to": a.assigned_to,
                "assigned_by": a.assigned_by,
                "status": a.status,
                "notes": a.notes,
                "created_at": a.created_at,
                "resolved_at": a.resolved_at,
            }
            for a in assignments
        ]
    }


@router.put("/{team_id}/assignments/{assignment_id}/resolve")
async def teams_resolve_assignment(team_id: int, assignment_id: int, req: ResolveRequest):
    if resolve_assignment(assignment_id, team_id, req.resolved_by):
        return {"status": "resolved", "assignment_id": assignment_id}
    raise HTTPException(status_code=404, detail="Assignment not found")


@router.get("/{team_id}/activity")
async def teams_activity(team_id: int, limit: int = 20):
    activity = get_team_activity(team_id, limit)
    return {"activity": activity}


@router.get("/{team_id}/stats")
async def teams_stats(team_id: int):
    stats = get_team_stats(team_id)
    return stats
