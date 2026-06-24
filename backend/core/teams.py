"""Team management — create teams, invite members, assign findings."""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from backend.core.database import get_db


@dataclass
class Team:
    id: int
    name: str
    slug: str
    owner: str
    plan: str
    created_at: float
    member_count: int = 0


@dataclass
class TeamMember:
    id: int
    team_id: int
    email: str
    name: Optional[str]
    role: str
    invited_at: float
    joined_at: Optional[float]


@dataclass
class FindingAssignment:
    id: int
    audit_id: int
    finding_id: str
    team_id: int
    assigned_to: str
    assigned_by: str
    status: str
    notes: Optional[str]
    created_at: float
    resolved_at: Optional[float]


def _slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return slug[:50]


def create_team(name: str, owner: str = "default") -> Team:
    slug = _slugify(name)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO teams (name, slug, owner) VALUES (?, ?, ?)",
            (name, slug, owner),
        )
        conn.commit()
        team_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO team_members (team_id, email, name, role, joined_at) VALUES (?, ?, ?, 'owner', ?)",
            (team_id, owner, owner, time.time()),
        )
        conn.commit()

    return Team(id=team_id, name=name, slug=slug, owner=owner, plan="team",
                created_at=time.time(), member_count=1)


def get_team(team_id: int) -> Optional[Team]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        if not row:
            return None
        members = conn.execute(
            "SELECT COUNT(*) as cnt FROM team_members WHERE team_id = ?", (team_id,)
        ).fetchone()
        return Team(
            id=row["id"], name=row["name"], slug=row["slug"],
            owner=row["owner"], plan=row["plan"], created_at=row["created_at"],
            member_count=members["cnt"],
        )


def list_teams(owner: str = "default") -> list[Team]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.*, (SELECT COUNT(*) FROM team_members WHERE team_id = t.id) as member_count
            FROM teams t
            WHERE t.owner = ? OR t.id IN (SELECT team_id FROM team_members WHERE email = ?)
            ORDER BY t.created_at DESC
        """, (owner, owner)).fetchall()

    return [Team(
        id=r["id"], name=r["name"], slug=r["slug"],
        owner=r["owner"], plan=r["plan"], created_at=r["created_at"],
        member_count=r["member_count"],
    ) for r in rows]


def add_member(team_id: int, email: str, name: str = None, role: str = "member") -> TeamMember:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO team_members (team_id, email, name, role) VALUES (?, ?, ?, ?)",
            (team_id, email, name, role),
        )
        conn.commit()
        member_id = cursor.lastrowid

    return TeamMember(
        id=member_id, team_id=team_id, email=email, name=name,
        role=role, invited_at=time.time(), joined_at=None,
    )


def list_members(team_id: int) -> list[TeamMember]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM team_members WHERE team_id = ? ORDER BY role, email",
            (team_id,),
        ).fetchall()

    return [TeamMember(
        id=r["id"], team_id=r["team_id"], email=r["email"],
        name=r["name"], role=r["role"], invited_at=r["invited_at"],
        joined_at=r["joined_at"],
    ) for r in rows]


def remove_member(team_id: int, email: str) -> bool:
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM team_members WHERE team_id = ? AND email = ? AND role != 'owner'",
            (team_id, email),
        )
        conn.commit()
        return result.rowcount > 0


def assign_finding(
    audit_id: int,
    finding_id: str,
    team_id: int,
    assigned_to: str,
    assigned_by: str = "default",
    notes: str = None,
) -> FindingAssignment:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO finding_assignments (audit_id, finding_id, team_id, assigned_to, assigned_by, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (audit_id, finding_id, team_id, assigned_to, assigned_by, notes))
        conn.commit()
        assignment_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO team_activity (team_id, actor, action, target, details)
            VALUES (?, ?, 'assigned', ?, ?)
        """, (team_id, assigned_by, finding_id, json.dumps({"assigned_to": assigned_to, "audit_id": audit_id})))
        conn.commit()

    return FindingAssignment(
        id=assignment_id, audit_id=audit_id, finding_id=finding_id,
        team_id=team_id, assigned_to=assigned_to, assigned_by=assigned_by,
        status="open", notes=notes, created_at=time.time(), resolved_at=None,
    )


def list_assignments(team_id: int, status: str = None) -> list[FindingAssignment]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM finding_assignments WHERE team_id = ? AND status = ? ORDER BY created_at DESC",
                (team_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM finding_assignments WHERE team_id = ? ORDER BY created_at DESC",
                (team_id,),
            ).fetchall()

    return [FindingAssignment(
        id=r["id"], audit_id=r["audit_id"], finding_id=r["finding_id"],
        team_id=r["team_id"], assigned_to=r["assigned_to"],
        assigned_by=r["assigned_by"], status=r["status"],
        notes=r["notes"], created_at=r["created_at"], resolved_at=r["resolved_at"],
    ) for r in rows]


def resolve_assignment(assignment_id: int, team_id: int, resolved_by: str = "default") -> bool:
    with get_db() as conn:
        result = conn.execute("""
            UPDATE finding_assignments SET status = 'resolved', resolved_at = ?
            WHERE id = ? AND team_id = ?
        """, (time.time(), assignment_id, team_id))
        conn.commit()

        if result.rowcount > 0:
            conn.execute("""
                INSERT INTO team_activity (team_id, actor, action, target)
                VALUES (?, ?, 'resolved', ?)
            """, (team_id, resolved_by, str(assignment_id)))
            conn.commit()
            return True
        return False


def get_team_activity(team_id: int, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM team_activity WHERE team_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (team_id, limit)).fetchall()

    return [dict(r) for r in rows]


def get_team_stats(team_id: int) -> dict:
    with get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM finding_assignments WHERE team_id = ?", (team_id,)
        ).fetchone()
        open_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM finding_assignments WHERE team_id = ? AND status = 'open'",
            (team_id,),
        ).fetchone()
        resolved_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM finding_assignments WHERE team_id = ? AND status = 'resolved'",
            (team_id,),
        ).fetchone()
        members = conn.execute(
            "SELECT COUNT(*) as cnt FROM team_members WHERE team_id = ?", (team_id,)
        ).fetchone()
        audits = conn.execute(
            "SELECT COUNT(DISTINCT audit_id) as cnt FROM finding_assignments WHERE team_id = ?",
            (team_id,),
        ).fetchone()

    return {
        "total_assignments": total["cnt"],
        "open": open_cnt["cnt"],
        "resolved": resolved_cnt["cnt"],
        "members": members["cnt"],
        "audits_with_assignments": audits["cnt"],
    }
