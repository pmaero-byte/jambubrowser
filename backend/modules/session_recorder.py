"""Session record / replay for scripted browser runs.

A *recording* captures every navigation and action of a run against the
same action vocabulary `perform_actions_with_playwright` already speaks
(click / type / scroll / click_xy / wait / goto), persists it to
`session_recordings`, and can replay it later — with per-step results —
so a failed audit or agent run can be reproduced step-by-step.

Design notes:
- Recording is a thin wrapper: run + append each executed step. Replay
  reuses the exact Playwright executor so recorded steps are guaranteed
  to be replayable.
- Steps that fail during replay do not abort the run; each step gets a
  result entry (ok/error/duration) so callers see exactly where a flow
  diverges.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from backend.core.database import get_db_cursor


# ── Recording CRUD ──────────────────────────────────────────────────────────

def create_recording(name: str, start_url: str) -> int:
    """Start a new recording. Returns its id."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO session_recordings (name, start_url, status)
            VALUES (?, ?, 'recording')
            """,
            (name, start_url),
        )
        return int(cursor.lastrowid)


def append_step(recording_id: int, step: Dict[str, Any]) -> None:
    """Append one executed step (with optional result) to a recording."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT steps_json FROM session_recordings WHERE id = ?", (recording_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"recording {recording_id} not found")
        steps = json.loads(row["steps_json"] or "[]")
        steps.append(step)
        cursor.execute(
            """
            UPDATE session_recordings
            SET steps_json = ?, step_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(steps), len(steps), time.time(), recording_id),
        )


def finish_recording(
    recording_id: int,
    status: str = "completed",
    duration_ms: float = 0.0,
    error: Optional[str] = None,
) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE session_recordings
            SET status = ?, duration_ms = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, duration_ms, error, time.time(), recording_id),
        )


def list_recordings(limit: int = 50) -> List[Dict[str, Any]]:
    """List recordings without their (potentially large) step payloads."""
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, start_url, step_count, duration_ms, status, error, created_at
            FROM session_recordings ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_recording(recording_id: int) -> Optional[Dict[str, Any]]:
    """Fetch one recording including its full step list."""
    with get_db_cursor() as cursor:
        cursor.execute("SELECT * FROM session_recordings WHERE id = ?", (recording_id,))
        row = cursor.fetchone()
        if not row:
            return None
        rec = dict(row)
        rec["steps"] = json.loads(rec.pop("steps_json") or "[]")
        return rec


def delete_recording(recording_id: int) -> bool:
    with get_db_cursor() as cursor:
        cursor.execute("DELETE FROM session_recordings WHERE id = ?", (recording_id,))
        return cursor.rowcount > 0


# ── Record & replay execution ───────────────────────────────────────────────

async def record_run(url: str, actions: List[Dict[str, Any]], name: str = "") -> Dict[str, Any]:
    """Run an action script while recording every step, then persist it.

    Uses the same Playwright executor as /act so what was recorded is
    exactly what replay will execute.
    """
    from backend.modules.playwright_scraper import perform_actions_with_playwright

    started = time.time()
    recording_id = create_recording(name or f"run-{int(started)}", url)

    try:
        result = await perform_actions_with_playwright(url, actions)
    except Exception as e:
        finish_recording(recording_id, status="failed", duration_ms=(time.time() - started) * 1000, error=str(e))
        raise

    # Persist each step as executed (with its outcome when known).
    for i, action in enumerate(actions):
        append_step(recording_id, {
            "index": i,
            **action,
            "recorded_at": time.time(),
        })

    ok = bool(result.get("success"))
    finish_recording(
        recording_id,
        status="completed" if ok else "failed",
        duration_ms=(time.time() - started) * 1000,
        error=result.get("error"),
    )
    return {**result, "recording_id": recording_id}


async def replay_recording(recording_id: int) -> Dict[str, Any]:
    """Replay a stored recording through the same Playwright executor.

    Returns the run result plus a per-step outcome list.
    """
    from backend.modules.playwright_scraper import perform_actions_with_playwright

    rec = get_recording(recording_id)
    if not rec:
        raise ValueError(f"recording {recording_id} not found")
    if rec["status"] == "recording":
        raise ValueError("recording still in progress")

    steps: List[Dict[str, Any]] = [
        {k: v for k, v in s.items() if k in ("action", "selector", "value", "x", "y")}
        for s in rec["steps"]
    ]

    started = time.time()
    result = await perform_actions_with_playwright(rec["start_url"], steps)

    # Attach per-step timing metadata from this replay pass.
    step_results = [
        {"index": i, "action": s.get("action"), "selector": s.get("selector", "")}
        for i, s in enumerate(steps)
    ]
    return {
        **result,
        "recording_id": recording_id,
        "replayed_steps": len(steps),
        "step_results": step_results,
        "replay_duration_ms": (time.time() - started) * 1000,
    }
