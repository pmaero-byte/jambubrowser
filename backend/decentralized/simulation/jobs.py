"""The job store: persistence, idempotency, submit, the durable queue and
the evidence bundle.

A job is written once it reaches a terminal state, except when it is
queued for the background worker. `submit` is the entry point every
caller uses — routes, CLI and MCP all go through it — and it owns
idempotency: a retried request with the same key returns the original
job rather than paying twice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from backend.core.database import get_db_cursor
from backend.decentralized.meshpay import js_dumps
from backend.decentralized.simulation.runner import quote, run_job
from backend.decentralized.simulation.types import (
    STATUS_FAILED,
    STATUS_QUARANTINED,
    SimulationSpec,
)

# One logger name for the whole subsystem, so a log filter on
# "jambu.simulation" still catches every module after the split.
log = logging.getLogger("jambu.simulation")

# ---------------------------------------------------------------------------
# Job store (idempotency + history)
# ---------------------------------------------------------------------------

def save_job(
    result: dict, *, job_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """Persist a run result and return the stored record.

    When ``idempotency_key`` matches an existing job the stored record is
    returned unchanged — that is the point: a retried submit must not bill a
    second time for work already dispatched.
    """
    from backend.core.database import get_db

    if idempotency_key:
        existing = get_job_by_idempotency_key(idempotency_key)
        if existing:
            return existing

    record = {
        "id": job_id or uuid.uuid4().hex,
        "idempotency_key": idempotency_key,
        "spec_hash": result["spec_hash"],
        "spec_json": js_dumps(result["spec"]),
        "status": result["status"],
        "tier": result["quote"]["tier"],
        "replicas": result["quote"]["replicates"],
        "quote_json": js_dumps(result["quote"]),
        "result_json": js_dumps(result),
        "charged_dct": float(result.get("charged_dct") or 0),
        "error": result.get("error"),
    }
    now = time.time()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO simulation_jobs
                (id, idempotency_key, spec_hash, spec_json, status, tier,
                 replicas, quote_json, result_json, charged_dct, error,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"], record["idempotency_key"], record["spec_hash"],
                record["spec_json"], record["status"], record["tier"],
                record["replicas"], record["quote_json"],
                record["result_json"], record["charged_dct"], record["error"],
                now, now,
            ),
        )
        conn.commit()
    # Return the same shape a later read produces, so a submit response and a
    # subsequent GET are directly comparable.
    return {
        **record,
        "spec": result["spec"],
        "quote": result["quote"],
        "result": result,
        "created_at": now,
        "updated_at": now,
    }


def _row_to_job(row) -> dict:
    record = dict(row)
    for blob, key in (("spec_json", "spec"), ("quote_json", "quote"),
                      ("result_json", "result")):
        try:
            record[key] = json.loads(record.pop(blob) or "{}")
        except ValueError:
            record[key] = {}
    return record


def get_job(job_id: str) -> Optional[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM simulation_jobs WHERE id = ?", (job_id,),
        ).fetchone()
    return _public_record(_row_to_job(row)) if row else None


def get_job_by_idempotency_key(key: str) -> Optional[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM simulation_jobs WHERE idempotency_key = ?", (key,),
        ).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    limit: int = 50, *, status: Optional[str] = None, spec_hash: Optional[str] = None,
) -> list[dict]:
    from backend.core.database import get_db

    query = "SELECT * FROM simulation_jobs"
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if spec_hash:
        clauses.append("spec_hash = ?")
        params.append(spec_hash)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_job(r) for r in rows]


def job_totals() -> dict:
    """Aggregate spend and outcome counts — the operator's headline numbers."""
    from backend.core.database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n, SUM(charged_dct) AS charged "
            "FROM simulation_jobs GROUP BY status"
        ).fetchall()
    by_status = {
        r["status"]: {"jobs": r["n"], "chargedDct": round(r["charged"] or 0, 9)}
        for r in rows
    }
    return {
        "by_status": by_status,
        "jobs": sum(v["jobs"] for v in by_status.values()),
        # Quarantined work is the number worth watching: it is work that was
        # dispatched, not paid, and usually means a node is misbehaving.
        "chargedDct": round(
            sum(v["chargedDct"] for v in by_status.values()), 9
        ),
        "unpaidJobs": by_status.get(STATUS_QUARANTINED, {}).get("jobs", 0)
        + by_status.get(STATUS_FAILED, {}).get("jobs", 0),
    }


# ---------------------------------------------------------------------------
# Submit (the route/CLI/MCP entry point)
# ---------------------------------------------------------------------------

def _public_record(stored: dict) -> dict:
    """One flat, self-describing job record for every surface.

    The store keeps ``result_json`` (the full run record) and the audit keeps
    scalar columns, but callers — routes, CLI, MCP — should not have to know
    which is which, so both shapes are merged here into a single payload.
    """
    result = stored.get("result") or {}
    quote_block = stored.get("quote") or result.get("quote") or {}
    verification = result.get("verification")
    return {
        "id": stored.get("id"),
        "idempotency_key": stored.get("idempotency_key"),
        "spec_hash": stored.get("spec_hash"),
        "spec": stored.get("spec") or result.get("spec"),
        "quote": quote_block,
        "tier": stored.get("tier") or quote_block.get("tier"),
        "replicas": stored.get("replicas", quote_block.get("replicates", 1)),
        "status": stored.get("status") or result.get("status"),
        "attempts": result.get("attempts", []),
        "verification": verification,
        "nodes_used": result.get("nodes_used", []),
        "required_tier_satisfied": result.get("required_tier_satisfied"),
        "charged_dct": stored.get("charged_dct", 0),
        "error": stored.get("error") or result.get("error"),
        "created_at": stored.get("created_at"),
        "updated_at": stored.get("updated_at"),
    }


async def submit(
    spec: SimulationSpec,
    *,
    replicates: int = 1,
    idempotency_key: Optional[str] = None,
    persist: bool = True,
) -> dict:
    """Freeze → quote → dispatch → verify → settle, then persist.

    ``idempotency_key`` short-circuits before dispatch: a retry returns the
    stored job and spends nothing, so a client that times out mid-flight and
    retries cannot be billed twice for the same work.
    """
    if idempotency_key and persist:
        existing = get_job_by_idempotency_key(idempotency_key)
        if existing:
            return {**_public_record(existing), "idempotent_replay": True}

    # Validate the replica count before any work is dispatched, so a bad
    # request never reaches the mesh.
    quote(spec, replicates=replicates)
    result = await run_job(spec, replicates=replicates)
    if not persist:
        return {**result, "id": uuid.uuid4().hex, "idempotent_replay": False}
    stored = save_job(result, idempotency_key=idempotency_key)
    return {**_public_record(stored), "idempotent_replay": False}


# ---------------------------------------------------------------------------
# Durable queue: enqueue now, settle later, survive a restart
# ---------------------------------------------------------------------------

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
OPEN_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})


def _insert_job(record: dict, *, idempotency_key: Optional[str]) -> dict:
    from backend.core.database import get_db

    now = time.time()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO simulation_jobs
                (id, idempotency_key, spec_hash, spec_json, status, tier,
                 replicas, quote_json, result_json, charged_dct, error,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"], idempotency_key, record["spec_hash"],
                js_dumps(record["spec"]), record["status"], record["tier"],
                record["replicas"], js_dumps(record["quote"]),
                js_dumps(record.get("result") or {}),
                record.get("charged_dct", 0.0), record.get("error"),
                now, now,
            ),
        )
        conn.commit()
    return {**record, "created_at": now, "updated_at": now}


def enqueue_job(
    spec: SimulationSpec,
    *,
    replicates: int = 1,
    idempotency_key: Optional[str] = None,
) -> dict:
    """Persist a QUEUED job without dispatching it.

    The spec is frozen and validated before anything is written, so a bad
    request never enters the queue. ``idempotency_key`` deduplicates before
    insert, exactly like the synchronous path.
    """
    if idempotency_key:
        existing = get_job_by_idempotency_key(idempotency_key)
        if existing:
            return {**_public_record(existing), "idempotent_replay": True}
    pricing = quote(spec, replicates=replicates)  # validates too
    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "idempotency_key": idempotency_key,
        "spec_hash": spec.spec_hash(),
        "spec": spec.canonical(),
        "status": STATUS_QUEUED,
        "tier": pricing["tier"],
        "replicas": replicates,
        "quote": pricing,
        "charged_dct": 0.0,
        "error": None,
        "result": {},
    }
    stored = _insert_job(record, idempotency_key=idempotency_key)
    return {**_public_record(stored), "idempotent_replay": False}


def _claim_next_queued() -> Optional[dict]:
    """Atomically flip the oldest QUEUED job to RUNNING and return it."""
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM simulation_jobs WHERE status = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (STATUS_QUEUED,),
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE simulation_jobs SET status = ?, updated_at = ? "
            "WHERE id = ? AND status = ?",
            (STATUS_RUNNING, time.time(), row["id"], STATUS_QUEUED),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None  # another worker claimed it first
        return _row_to_job(row)


def _finish_job(job_id: str, result: dict) -> None:
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute(
            "UPDATE simulation_jobs SET status = ?, result_json = ?, "
            "charged_dct = ?, error = ?, tier = ?, updated_at = ? WHERE id = ?",
            (
                result["status"],
                js_dumps(result),
                float(result.get("charged_dct") or 0),
                result.get("error"),
                (result.get("quote") or {}).get("tier"),
                time.time(),
                job_id,
            ),
        )
        conn.commit()


def recover_interrupted_jobs() -> int:
    """Re-queue jobs a dead process left in RUNNING. Returns the count."""
    from backend.core.database import get_db

    with get_db() as conn:
        cur = conn.execute(
            "UPDATE simulation_jobs SET status = ?, updated_at = ? WHERE status = ?",
            (STATUS_QUEUED, time.time(), STATUS_RUNNING),
        )
        conn.commit()
        return cur.rowcount


class SimulationWorker:
    """FIFO worker that drains the durable queue inside the engine process.

    One job at a time by design: a node that is mid-job is known-busy, and
    the honesty constraint says we must not pretend to field more capacity
    than we actually have. Start it with the engine lifespan; without it the
    synchronous path is the only way jobs run.
    """

    def __init__(self, *, poll: float = 2.0):
        self._poll = max(0.1, float(poll))
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        recover_interrupted_jobs()
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_one(self) -> bool:
        row = _claim_next_queued()
        if row is None:
            return False
        try:
            spec = SimulationSpec.from_dict(row["spec"])
            result = await run_job(spec, replicates=row.get("replicas") or 1)
        except Exception as exc:  # noqa: BLE001 - surface, never kill the loop
            log.warning("queued simulation job %s failed: %s", row["id"], exc)
            result = {
                "spec_hash": row["spec_hash"],
                "spec": row.get("spec") or {},
                "quote": row.get("quote") or {},
                "status": STATUS_FAILED,
                "attempts": [],
                "verification": None,
                "error": str(exc),
                "charged_dct": 0.0,
                "nodes_used": [],
                "required_tier_satisfied": False,
                "excluded_nodes": {},
            }
        _finish_job(row["id"], result)
        return True

    async def _loop(self) -> None:
        while True:
            try:
                did = await self.run_one()
            except Exception as exc:  # noqa: BLE001 - the loop must not die
                log.warning("simulation worker loop error: %s", exc)
                did = False
            if not did:
                await asyncio.sleep(self._poll)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def simulation_bundle(job_id: str) -> dict:
    """Sign one job into an Ed25519 evidence bundle (kind compute_simulation).

    The bundle carries the frozen spec, the spec hash, every node's
    execution hash, and the numeric verification report — so a third party
    can re-derive what was asked, who answered, and whether they agreed,
    without trusting this process. Verify with
    ``scripts/verify_evidence_bundle.py``.
    """
    from backend.decentralized.evidence import build_bundle

    # get_job returns the flattened public record, so read attempts from it.
    job = get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    verification = job.get("verification") or {}
    payload = {
        "job_id": job["id"],
        "spec_hash": job["spec_hash"],
        "spec": job.get("spec"),
        "module_digest": (job.get("spec") or {}).get("module_digest", ""),
        "tier": job.get("tier"),
        "replicas": job.get("replicas"),
        "status": job["status"],
        "charged_dct": job.get("charged_dct", 0),
        "quote": job.get("quote"),
        "verification": verification or None,
        "nodes_used": job.get("nodes_used", []),
        "execution_hashes": {
            a["node_id"]: a.get("execution_hash")
            for a in job.get("attempts") or []
        },
        "error": job.get("error"),
    }
    # Egress provenance: the network environment this job was verified in.
    try:
        from backend.core.vpn import get_vpn_manager

        manager = get_vpn_manager()
        payload["egress"] = {
            "enabled": manager.enabled,
            "tunnel_kind": manager.config.tunnel_kind.value,
            "pq_requested": manager.config.pq or "",
            "pool_endpoints": manager.pool.size,
        }
    except Exception:  # never break evidence for VPN plumbing
        payload["egress"] = {"enabled": False}
    return build_bundle(
        "compute_simulation",
        {
            "job_id": job["id"],
            "status": job["status"],
            "verdict": verification.get("verdict"),
        },
        payload,
    )
