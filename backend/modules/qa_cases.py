"""QA test cases — the managed layer of the AI QA team (Milestone 1).

Flows are scripts. Cases are *managed tests*: a goal + steps + severity +
owner, every run persisted with a verdict, and selector rot recorded as
propose-only heal events a human disposes.

Execution: run the case steps once; for each failed step that shipped
resolver candidates, retry just that step through a fresh one-shot run;
a retry that passes becomes a ``proposed`` heal event — stored case steps
are NEVER rewritten implicitly. Verdict counts a healed step as passed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import urlparse

from backend.core.database import get_db

log = logging.getLogger("jambu.qa_cases")

QA_KINDS = (
    "smoke", "login", "signup", "checkout", "search",
    "accessibility", "performance", "responsive",
)
QA_SEVERITIES = ("critical", "high", "medium", "low", "info")
RUN_HISTORY_KEEP = 50
_HEALABLE_REASONS = ("target_not_found", "target_ambiguous", "unknown_ref")

# Flake policy — a gate nobody trusts is worthless, so flakes are measured
# and quarantined automatically instead of silently retried forever.
FLAKY_QUARANTINE_AFTER = int(os.environ.get("JAMBU_QA_FLAKY_AFTER", "3"))
PROMOTE_AFTER_GREEN_STREAK = int(os.environ.get("JAMBU_QA_PROMOTE_STREAK", "5"))


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _load(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _row_to_case(row) -> dict:
    base = {
        "id": row["id"], "name": row["name"], "url": row["url"],
        "goal": row["goal"] or "", "kind": row["kind"] or "smoke",
        "steps": _load(row["steps"], []),
        "severity": row["severity"] or "medium",
        "owner": row["owner"] or "", "enabled": bool(row["enabled"]),
        "procedural_key": row["procedural_key"] or "",
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"],
    }
    try:
        base["dataset_id"] = row["dataset_id"]
    except (KeyError, IndexError):
        base["dataset_id"] = None
    for extra, default in (("quarantined", False), ("quarantine_reason", None),
                           ("flaky_count", 0), ("consecutive_passes", 0),
                           ("auto_retry", True)):
        try:
            value = row[extra]
        except (KeyError, IndexError):
            value = default
        if extra in ("quarantined", "auto_retry"):
            value = bool(value)
        base[extra] = value
    try:
        base["placeholders"] = placeholders_of(base["steps"])
    except Exception:
        base["placeholders"] = []
    return base


def _row_to_run(row) -> dict:
    base = {
        "id": row["id"], "case_id": row["case_id"],
        "run_at": row["run_at"], "status": row["status"],
        "ok": bool(row["ok"]), "passed": row["passed"],
        "failed": row["failed"], "total": row["total"],
        "duration_ms": row["duration_ms"],
        "healed_steps": row["healed_steps"],
        "failed_steps": _load(row["failed_steps"], []),
        "console_errors": _load(row["console_errors"], []),
        "healed_events": _load(row["healed_events"], []),
        "artifacts": _load(row["artifacts"], {}),
        "report": _load(row["report"], {}),
        "error": row["error"],
    }
    for extra in ("dataset_rows", "dataset_index", "attempt"):
        try:
            base[extra] = row[extra]
        except (KeyError, IndexError):
            base[extra] = 0 if extra == "dataset_rows" else (
                1 if extra == "attempt" else None)
    try:
        base["flaky"] = bool(row["flaky"])
    except (KeyError, IndexError):
        base["flaky"] = False
    try:
        base["variant"] = row["variant"]
    except (KeyError, IndexError):
        base["variant"] = None
    return base


def _row_to_heal(row) -> dict:
    return {
        "id": row["id"], "case_id": row["case_id"],
        "run_id": row["run_id"], "step_index": row["step_index"],
        "old_target": row["old_target"] or "",
        "new_target": row["new_target"] or "",
        "new_ref": row["new_ref"] or "",
        "strategy": row["strategy"] or "resnapshot",
        "status": row["status"] or "proposed",
        "created_at": row["created_at"], "decided_at": row["decided_at"],
        "decided_by": row["decided_by"] or "",
    }


def _normalise_kind(kind: str | None) -> str:
    k = (kind or "smoke").strip().lower()
    return k if k in QA_KINDS else "smoke"


def _normalise_severity(severity: str | None) -> str:
    s = (severity or "medium").strip().lower()
    return s if s in QA_SEVERITIES else "medium"


def placeholders_of(steps: list[dict]) -> list[str]:
    from backend.modules.qa_datasets import placeholders_in_steps

    return placeholders_in_steps(steps)

def create_case(name: str, url: str, steps: list[dict], *,
                goal: str = "", kind: str = "smoke",
                severity: str = "medium", owner: str = "",
                enabled: bool = True,
                dataset_id: Optional[int] = None) -> dict:
    """Create a managed test case. Steps are stored verbatim (JSON)."""
    if not name or not name.strip():
        raise ValueError("name must be non-empty")
    if not url or not url.strip():
        raise ValueError("url must be non-empty")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    if dataset_id is not None:
        from backend.modules.qa_datasets import get_dataset

        if get_dataset(dataset_id) is None:
            raise ValueError(f"no such dataset: {dataset_id}")
    kind = _normalise_kind(kind)
    procedural_key = f"qa:{kind}:{_host(url)}"
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO qa_cases
                (name, url, goal, kind, steps, severity, owner, enabled,
                 procedural_key, dataset_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), url.strip(), goal or "", kind,
             json.dumps(steps), _normalise_severity(severity),
             owner or "", 1 if enabled else 0, procedural_key,
             dataset_id),
        )
        conn.commit()
        case_id = cur.lastrowid
    return get_case(case_id)


def get_case(case_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM qa_cases WHERE id = ?", (case_id,)
        ).fetchone()
    return _row_to_case(row) if row else None


def list_cases(*, enabled_only: bool = False) -> list[dict]:
    with get_db() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT * FROM qa_cases WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM qa_cases ORDER BY id").fetchall()
    return [_row_to_case(r) for r in rows]


def update_case(case_id: int, **fields) -> Optional[dict]:
    allowed = {"name", "url", "goal", "kind", "steps", "severity",
               "owner", "enabled", "dataset_id"}
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "steps":
            if not isinstance(value, list) or not value:
                raise ValueError("steps must be a non-empty list")
            value = json.dumps(value)
        elif key == "kind":
            value = _normalise_kind(value)
        elif key == "severity":
            value = _normalise_severity(value)
        elif key == "enabled":
            value = 1 if value else 0
        elif key == "dataset_id":
            if value is not None:
                from backend.modules.qa_datasets import get_dataset

                if get_dataset(int(value)) is None:
                    raise ValueError(f"no such dataset: {value}")
            value = None if value is None else int(value)
        updates[key] = value
    if updates:
        row = get_case(case_id)
        url = str(updates.get("url", row["url"] if row else ""))
        kind = str(updates.get("kind", row["kind"] if row else "smoke"))
        updates["procedural_key"] = f"qa:{kind}:{_host(url)}"
        updates["updated_at"] = time.time()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        with get_db() as conn:
            conn.execute(
                f"UPDATE qa_cases SET {assignments} WHERE id = ?",
                (*updates.values(), case_id),
            )
            conn.commit()
    return get_case(case_id)


def delete_case(case_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM qa_runs WHERE case_id = ?", (case_id,))
        cur = conn.execute(
            "DELETE FROM qa_cases WHERE id = ?", (case_id,))
        conn.commit()
        return cur.rowcount > 0

def propose_heal_event(case_id: int, run_id: Optional[int], *,
                       step_index: int, old_target: str,
                       new_target: str, new_ref: str = "",
                       strategy: str = "resnapshot") -> dict:
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO qa_heal_events
                (case_id, run_id, step_index, old_target, new_target,
                 new_ref, strategy, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed')
            """,
            (case_id, run_id, step_index, old_target or "",
             new_target or "", new_ref or "", strategy),
        )
        conn.commit()
        event_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM qa_heal_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_heal(row)


def list_heal_events(case_id: Optional[int] = None, *,
                     status: Optional[str] = None,
                     limit: int = 50) -> list[dict]:
    query = "SELECT * FROM qa_heal_events"
    clauses: list[str] = []
    params: list[Any] = []
    if case_id is not None:
        clauses.append("case_id = ?")
        params.append(case_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_heal(r) for r in rows]


def decide_heal_event(event_id: int, *, accept: bool,
                      actor: str = "qa-lead") -> Optional[dict]:
    """Accept or reject a proposed heal.

    Accepting rewrites the stored case step's target to the healed value
    (the ONLY path that mutates case steps). Rejecting leaves the case
    untouched. Already-disposed events return as-is (idempotent).
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM qa_heal_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return None
        event = _row_to_heal(row)
        if event["status"] != "proposed":
            return event
        new_status = "accepted" if accept else "rejected"
        conn.execute(
            "UPDATE qa_heal_events SET status = ?, decided_at = ?, "
            "decided_by = ? WHERE id = ?",
            (new_status, time.time(), actor or "", event_id),
        )
        if accept:
            case = get_case(event["case_id"])
            if case is not None:
                steps = case["steps"]
                idx = event["step_index"]
                if 0 <= idx < len(steps) and isinstance(steps[idx], dict):
                    step = dict(steps[idx])
                    healed = event["new_target"] or event["new_ref"]
                    if healed and "target" in step:
                        step["target"] = healed
                        steps[idx] = step
                        conn.execute(
                            "UPDATE qa_cases SET steps = ?, updated_at = ? "
                            "WHERE id = ?",
                            (json.dumps(steps), time.time(), case["id"]),
                        )
                    elif healed and "name" in step:
                        step["name"] = healed
                        steps[idx] = step
                        conn.execute(
                            "UPDATE qa_cases SET steps = ?, updated_at = ? "
                            "WHERE id = ?",
                            (json.dumps(steps), time.time(), case["id"]),
                        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM qa_heal_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_heal(row)

def _run_one_shot(url: str, probe_steps: list[dict], *, local: bool,
                  approve: bool) -> dict:
    """Run a tiny probe flow in a fresh one-shot session (heal retry)."""
    import asyncio
    import concurrent.futures

    from backend.modules.browser_agent import BrowserAgentService

    async def _go() -> dict:
        service = BrowserAgentService()
        return await service.run_test(
            url=url, steps=probe_steps, local=local, approve=approve)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_go())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _go()).result()


def _attempt_heal(step: dict, failed: dict, url: str, *, local: bool,
                  approve: bool) -> Optional[dict]:
    """Try each resolver candidate as a replacement target."""
    candidates = failed.get("candidates") or []
    if not candidates:
        return None
    if ("target" not in step) and ("name" not in step):
        return None
    for cand in candidates[:5]:
        new_ref = str(cand.get("ref") or "")
        new_target = str(cand.get("name") or new_ref)
        if not new_target:
            continue
        retried = dict(step)
        retried.pop("ref", None)
        if "target" in step:
            retried["target"] = new_target
        else:
            retried["name"] = new_target
        probe = [{"action": "navigate", "url": url}, retried]
        try:
            result = _run_one_shot(url, probe, local=local, approve=approve)
        except Exception:
            log.debug("heal probe failed for %r", new_target, exc_info=True)
            continue
        if result.get("ok"):
            return {"new_target": new_target, "new_ref": new_ref,
                    "retried_step": retried}
    return None


def _record_procedural(case: dict, success: bool,
                       duration_ms: float) -> None:
    """Feed the case verdict into procedural memory (best-effort)."""
    try:
        from backend.memory import get_memory

        store = get_memory()
        proc = store.get_or_create_procedural(
            "qa-team", case["procedural_key"] or f"qa:{case['kind']}",
            approach=f"case:{case['id']}",
        )
        store.record_procedural_outcome(proc.id, success=success,
                                        duration_ms=duration_ms)
    except Exception:
        log.debug("procedural record skipped", exc_info=True)

async def run_case(case_id: int, *, local: bool = False,
                   approve: bool = False, stop_on_failure: bool = False,
                   trace: bool = False, har: bool = False,
                   video: bool = False,
                   actor: str = "qa-team",
                   dataset_rows: Optional[list[dict]] = None,
                   viewport_matrix: Optional[list[dict]] = None,
                   junit: bool = False,
                   force: bool = False) -> dict:
    """Execute a case with verify → heal → retry; persist the verdict.

    ``dataset_rows`` runs the data matrix (one verdict per row, placeholders
    bound per row). ``viewport_matrix`` composes with it: the grid is
    dataset rows × viewport variants, cells run concurrently (capped at the
    session limit) and each verdict is persisted with its variant label.
    ``junit=True`` attaches a JUnit XML rendering of the persisted verdicts
    for CI consumption.
    """
    from backend.modules.browser_agent import BrowserAgentService
    from backend.modules.qa_datasets import (
        bind_placeholders, get_dataset, runs_to_junit)

    case = get_case(case_id)
    if case is None:
        raise ValueError(f"no such QA case: {case_id}")
    if not case["enabled"]:
        raise ValueError(f"QA case {case_id} is disabled")
    if case.get("quarantined") and not force:
        raise ValueError(
            f"QA case {case_id} is quarantined "
            f"({case.get('quarantine_reason') or 'flaky'}); "
            "pass force=true to run it anyway")

    rows = dataset_rows
    if rows is None and case.get("dataset_id"):
        dataset = get_dataset(case["dataset_id"])
        if dataset is None:
            raise ValueError(
                f"case dataset {case['dataset_id']} not found")
        rows = dataset["rows"]
    if rows is not None:
        if not isinstance(rows, list) or not rows:
            raise ValueError("dataset_rows must be a non-empty list")
        from backend.modules.qa_datasets import MAX_DATASET_ROWS

        if len(rows) > MAX_DATASET_ROWS:
            raise ValueError(f"max {MAX_DATASET_ROWS} rows per run")

    attempts_allowed = 2 if case.get("auto_retry", True) else 1

    _MATRIX_OPTION_KEYS = ("viewport", "locale", "user_agent",
                           "device_scale_factor", "timezone_id",
                           "color_scheme", "is_mobile", "has_touch")

    async def _one(bound_steps: list[dict], unbound: list[str],
                   index: Optional[int], total_rows: int,
                   *, context_options: Optional[dict] = None,
                   variant: Optional[str] = None) -> dict:
        """Run once; if it fails and auto-retry is on, retry once.

        A retry that passes makes the verdict ``flaky`` — green for the
        gate, but counted and eventually quarantined.
        """
        outcomes: list[dict] = []
        for attempt in range(1, attempts_allowed + 1):
            outcome = await _execute_bound(
                case, bound_steps, unbound, local=local, approve=approve,
                stop_on_failure=stop_on_failure, trace=trace, har=har,
                video=video, actor=actor, dataset_rows=total_rows,
                dataset_index=index, junit=junit and total_rows == 0,
                attempt=attempt, context_options=context_options,
                variant=variant)
            outcomes.append(outcome)
            if outcome["ok"]:
                break
        last = outcomes[-1]
        if len(outcomes) > 1 and last["ok"]:
            _mark_flaky(last["run_id"], attempts=len(outcomes))
            last = {**last, "flaky": True, "status": "flaky",
                    "attempts": len(outcomes),
                    "junit": last.get("junit")}
            if junit and total_rows == 0:
                from backend.modules.qa_datasets import runs_to_junit

                last["junit"] = runs_to_junit(case["name"],
                                              [get_run(last["run_id"])])
        else:
            last = {**last, "flaky": False, "attempts": len(outcomes)}
        return last

    if not rows and not viewport_matrix:
        result = await _one(case["steps"], [], None, 0)
        result["health"] = _update_case_health(case_id)
        if result["health"].get("quarantined"):
            result["quarantine_reason"] = result["health"]["quarantine_reason"]
        return result

    from backend.modules.qa_datasets import bind_placeholders as _bind

    # Viewport variants: one entry per browser context (name + Playwright
    # context options), same vocabulary as BrowserAgentService.run_matrix.
    variants: list[dict] = []
    if viewport_matrix:
        if not isinstance(viewport_matrix, list):
            raise ValueError("viewport_matrix must be a list")
        for v in viewport_matrix:
            if not isinstance(v, dict):
                raise ValueError("viewport_matrix entries must be objects")
            name = str(v.get("name") or f"variant-{len(variants) + 1}")
            context_options = {
                k: v[k] for k in _MATRIX_OPTION_KEYS if v.get(k) is not None
            }
            variants.append({"name": name, "context_options": context_options})
        from backend.modules.browser_agent import BrowserAgentService

        max_parallel = max(1, BrowserAgentService().max_sessions)
        if len(variants) * max(1, len(rows or [])) > max_parallel * 16:
            raise ValueError(
                f"matrix too large: {len(variants)} variants × "
                f"{len(rows or [])} rows exceeds {max_parallel * 16} cells")

    host = _host(case["url"])
    if variants:
        # Cross product: dataset rows × viewport variants, cells run
        # concurrently under the same session cap as run_matrix. Each
        # cell is a first-class persisted run tagged with its variant.
        row_list = rows or [None]
        semaphore = asyncio.Semaphore(max(1, max_parallel))

        async def _cell(idx: Optional[int], row: Optional[dict],
                        variant: dict) -> dict:
            if row is None:
                bound, unbound = case["steps"], []
            else:
                bound, unbound = _bind(case["steps"], row, host)
            async with semaphore:
                return await _one(
                    bound, unbound, idx,
                    0 if row is None else len(row_list),
                    context_options=variant["context_options"],
                    variant=variant["name"])

        outcomes = await asyncio.gather(
            *(_cell(None if row is None else idx, row, v)
              for v in variants
              for idx, row in enumerate(row_list)),
            return_exceptions=False,
        )
        outcomes = list(outcomes)
        by_variant: dict[str, dict] = {}
        variant_names = [v["name"] for v in variants for _ in row_list]
        for vname, cell in zip(variant_names, outcomes):
            bucket = by_variant.setdefault(vname, {
                "variant": vname, "cells": 0, "passed": 0,
                "failed": 0, "flaky": 0, "ok": True})
            bucket["cells"] += 1
            bucket["passed"] += 1 if cell["ok"] else 0
            bucket["failed"] += 0 if cell["ok"] else 1
            bucket["flaky"] += 1 if cell.get("flaky") else 0
            bucket["ok"] = bucket["ok"] and bool(cell["ok"])
        ok_all = all(o["ok"] for o in outcomes)
        summary = {
            "case_id": case_id, "matrix": True,
            "cells": len(outcomes),
            "rows": len(row_list), "variants": len(variants),
            "passed_cells": sum(1 for o in outcomes if o["ok"]),
            "failed_cells": sum(1 for o in outcomes if not o["ok"]),
            "flaky_cells": sum(1 for o in outcomes if o.get("flaky")),
            "by_variant": list(by_variant.values()),
            "ok": ok_all, "status": "passed" if ok_all else "failed",
            "runs": outcomes,
            "run_ids": [o["run_id"] for o in outcomes],
            "actor": actor,
            "health": _update_case_health(case_id),
        }
        if junit:
            summary["junit"] = runs_to_junit(
                case["name"],
                [get_run(o["run_id"]) for o in outcomes])
        return summary

    host = _host(case["url"])
    outcomes: list[dict] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"dataset row {idx} must be an object")
        bound, unbound = _bind(case["steps"], row, host)
        outcomes.append(await _one(bound, unbound, idx, len(rows)))
    ok_all = all(o["ok"] for o in outcomes)
    summary = {
        "case_id": case_id, "matrix": True,
        "rows": len(outcomes),
        "passed_rows": sum(1 for o in outcomes if o["ok"]),
        "failed_rows": sum(1 for o in outcomes if not o["ok"]),
        "flaky_rows": sum(1 for o in outcomes if o.get("flaky")),
        "ok": ok_all, "status": "passed" if ok_all else "failed",
        "runs": outcomes,
        "run_ids": [o["run_id"] for o in outcomes],
        "actor": actor,
        "health": _update_case_health(case_id),
    }
    if junit:
        summary["junit"] = runs_to_junit(
            case["name"],
            [get_run(o["run_id"]) for o in outcomes])
    return summary


async def _execute_bound(case: dict, bound_steps: list[dict],
                         unbound: list[str], *, local: bool,
                         approve: bool, stop_on_failure: bool,
                         trace: bool, har: bool, video: bool,
                         actor: str, dataset_rows: int,
                         dataset_index: Optional[int],
                         junit: bool = False,
                         attempt: int = 1,
                         context_options: Optional[dict] = None,
                         variant: Optional[str] = None) -> dict:
    """Run one bound step list through verify → heal → retry."""
    from backend.modules.browser_agent import BrowserAgentService

    case_id = case["id"]
    started = time.time()
    report: dict = {}
    error: Optional[str] = None
    try:
        service = BrowserAgentService()
        report = await service.run_test(
            url=case["url"], steps=bound_steps, local=local,
            approve=approve, stop_on_failure=stop_on_failure,
            trace=trace, har=har, video=video,
            context_options=context_options or {},
        )
    except Exception as exc:
        error = str(exc)[:300]
        log.warning("QA case %s failed to run", case_id, exc_info=True)

    healed_events: list[dict] = []
    healed_steps = 0
    failed_steps: list[dict] = []
    if report:
        for s in report.get("steps") or []:
            if s.get("status") == "failed":
                failed_steps.append({
                    "i": s.get("i"), "action": s.get("action"),
                    "reason": s.get("reason"), "error": s.get("error"),
                })
    if report and not stop_on_failure:
        by_i = {s.get("i"): s for s in report.get("steps") or []}
        for idx, step in enumerate(bound_steps):
            if not isinstance(step, dict):
                continue
            failed = by_i.get(idx + 1)
            if not failed or failed.get("status") != "failed":
                continue
            if failed.get("reason") not in _HEALABLE_REASONS:
                continue
            heal = _attempt_heal(step, failed, case["url"], local=local,
                                 approve=approve)
            if heal is None:
                continue
            healed_steps += 1
            healed_events.append({
                "step_index": idx,
                "old_target": str(step.get("target") or step.get("name")
                                  or step.get("ref") or ""),
                "new_target": heal["new_target"],
                "new_ref": heal["new_ref"],
                "strategy": "resnapshot",
            })
            failed["status"] = "healed"
            failed["healed_to"] = heal["new_target"]
        passed = sum(1 for s in report.get("steps") or []
                     if s.get("status") in ("passed", "healed"))
        total = report.get("total", len(bound_steps))
        report["passed"] = passed
        report["failed"] = total - passed
        report["ok"] = report["failed"] == 0
        report["healed_steps"] = healed_steps

    ok = bool(report.get("ok")) if report else False
    if unbound:
        ok = False
    status = "passed" if ok else ("error" if error else "failed")
    if unbound and not error and status == "failed":
        error = f"unbound placeholders: {', '.join(unbound)}"
    duration_ms = int((time.time() - started) * 1000)

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO qa_runs
                (case_id, status, ok, passed, failed, total, duration_ms,
                 healed_steps, failed_steps, console_errors, healed_events,
                 artifacts, report, error, dataset_rows, dataset_index,
                 attempt, flaky, variant)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (case_id, status, 1 if ok else 0,
             report.get("passed", 0), report.get("failed", 0),
             report.get("total", 0),
             report.get("duration_ms", duration_ms),
             healed_steps, json.dumps(failed_steps),
             json.dumps((report.get("console_errors") or [])[:10]),
             json.dumps(healed_events),
             json.dumps(report.get("artifacts") or {}),
             json.dumps(report), error, dataset_rows, dataset_index,
             attempt, 0, variant),
        )
        run_id = cur.lastrowid
        conn.execute(
            "UPDATE qa_cases SET last_run_at = ?, last_status = ?, "
            "updated_at = ? WHERE id = ?",
            (time.time(), status, time.time(), case_id),
        )
        conn.execute(
            "DELETE FROM qa_runs WHERE case_id = ? AND id NOT IN ("
            "SELECT id FROM qa_runs WHERE case_id = ? "
            "ORDER BY run_at DESC LIMIT ?)",
            (case_id, case_id, RUN_HISTORY_KEEP),
        )
        conn.commit()

    persisted: list[dict] = []
    for heal in healed_events:
        persisted.append(propose_heal_event(
            case_id, run_id, step_index=heal["step_index"],
            old_target=heal["old_target"], new_target=heal["new_target"],
            new_ref=heal["new_ref"], strategy=heal["strategy"]))
    if persisted:
        with get_db() as conn:
            conn.execute(
                "UPDATE qa_runs SET healed_events = ? WHERE id = ?",
                (json.dumps([{"id": h["id"], **h} for h in persisted]),
                 run_id),
            )
            conn.commit()

    _record_procedural(case, ok, duration_ms)

    from backend.modules.qa_datasets import runs_to_junit as _to_junit

    result = {
        "case_id": case_id, "run_id": run_id, "status": status,
        "ok": ok, "passed": report.get("passed", 0) if report else 0,
        "failed": report.get("failed", 0) if report else 0,
        "total": report.get("total", 0) if report else 0,
        "healed_steps": healed_steps, "heals": persisted,
        "failed_steps": failed_steps,
        "console_errors": (report.get("console_errors") or [])[:5],
        "duration_ms": report.get("duration_ms", duration_ms),
        "actor": actor, "dataset_rows": dataset_rows,
        "dataset_index": dataset_index, "unbound": unbound,
        "variant": variant,
    }
    if junit:
        result["junit"] = _to_junit(case["name"], [get_run(run_id)])
    return result

def list_runs(case_id: int, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM qa_runs WHERE case_id = ? "
            "ORDER BY run_at DESC, id DESC LIMIT ?",
            (case_id, limit),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def get_run(run_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM qa_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return _row_to_run(row) if row else None


def _mark_flaky(run_id: int, *, attempts: int) -> None:
    """Flag a run whose pass only came from the retry."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT case_id FROM qa_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE qa_runs SET status = 'flaky', flaky = 1, "
            "attempt = ? WHERE id = ?", (attempts, run_id))
        conn.execute(
            "UPDATE qa_cases SET last_status = 'flaky' WHERE id = ?",
            (row["case_id"],))
        conn.commit()


def _count_flakes(case_id: int, *, window: int = 10) -> int:
    """Flaky runs in the recent window."""
    runs = list_runs(case_id, limit=window)
    return sum(1 for r in runs if r.get("flaky"))


def _green_streak(case_id: int, *, window: int = 20) -> int:
    """Consecutive passing (non-flaky) runs, newest first."""
    streak = 0
    for run in list_runs(case_id, limit=window):
        if run["ok"] and not run.get("flaky"):
            streak += 1
        else:
            break
    return streak


def evaluate_case_health(case_id: int) -> dict:
    """Auto-quarantine flaky cases; auto-promote after a green streak.

    Returns the case's health snapshot plus the action taken (if any).
    """
    case = get_case(case_id)
    if case is None:
        raise ValueError(f"no such QA case: {case_id}")
    flakes = _count_flakes(case_id)
    streak = _green_streak(case_id)
    action = None
    quarantined = bool(case.get("quarantined"))
    reason = case.get("quarantine_reason")

    if not quarantined and flakes >= FLAKY_QUARANTINE_AFTER:
        quarantined, reason = True, f"{flakes} flakes in the last 10 runs"
        action = "quarantined"
    elif quarantined and streak >= PROMOTE_AFTER_GREEN_STREAK:
        quarantined, reason = False, None
        action = "promoted"

    with get_db() as conn:
        conn.execute(
            "UPDATE qa_cases SET quarantined = ?, quarantine_reason = ?, "
            "flaky_count = ?, consecutive_passes = ?, updated_at = ? "
            "WHERE id = ?",
            (1 if quarantined else 0, reason, flakes, streak, time.time(),
             case_id),
        )
        conn.commit()

    return {
        "case_id": case_id, "quarantined": quarantined,
        "quarantine_reason": reason, "flaky_count": flakes,
        "consecutive_passes": streak, "action": action,
    }


_update_case_health = evaluate_case_health


def quarantine_case(case_id: int, reason: str = "",
                    actor: str = "qa-lead") -> Optional[dict]:
    """Manually park a case (excluded from gates until promoted)."""
    if get_case(case_id) is None:
        return None
    with get_db() as conn:
        conn.execute(
            "UPDATE qa_cases SET quarantined = 1, quarantine_reason = ?, "
            "updated_at = ? WHERE id = ?",
            (reason or f"quarantined by {actor}", time.time(), case_id),
        )
        conn.commit()
    return get_case(case_id)


def unquarantine_case(case_id: int, reason: str = "") -> Optional[dict]:
    if get_case(case_id) is None:
        return None
    with get_db() as conn:
        conn.execute(
            "UPDATE qa_cases SET quarantined = 0, quarantine_reason = NULL, "
            "consecutive_passes = 0, updated_at = ? WHERE id = ?",
            (time.time(), case_id),
        )
        conn.commit()
    return get_case(case_id)


def set_auto_retry(case_id: int, enabled: bool) -> Optional[dict]:
    if get_case(case_id) is None:
        return None
    with get_db() as conn:
        conn.execute(
            "UPDATE qa_cases SET auto_retry = ? WHERE id = ?",
            (1 if enabled else 0, case_id),
        )
        conn.commit()
    return get_case(case_id)


def case_stats(case_id: int, *, window: int = 20) -> dict:
    """Pass rate + heal rate + flake rate over the recent window."""
    runs = list_runs(case_id, limit=window)
    case = get_case(case_id) or {}
    if not runs:
        return {"case_id": case_id, "runs": 0, "pass_rate": None,
                "heal_rate": None, "flake_rate": None,
                "quarantined": bool(case.get("quarantined")),
                "quarantine_reason": case.get("quarantine_reason"),
                "last_status": None}
    passed = sum(1 for r in runs if r["ok"])
    flaky = sum(1 for r in runs if r.get("flaky"))
    healed = sum(r["healed_steps"] for r in runs)
    steps = sum(r["total"] for r in runs) or 1
    return {
        "case_id": case_id, "runs": len(runs),
        "pass_rate": round(passed / len(runs), 4),
        "heal_rate": round(healed / steps, 4),
        "healed_steps_total": healed,
        "flake_rate": round(flaky / len(runs), 4),
        "flaky_runs": flaky,
        "quarantined": bool(case.get("quarantined")),
        "quarantine_reason": case.get("quarantine_reason"),
        "consecutive_passes": case.get("consecutive_passes", 0),
        "last_status": runs[0]["status"],
    }





