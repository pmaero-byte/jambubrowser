"""
Simulation compute routes — price, dispatch, verify, settle.

Flow: ``POST /simulation/quote`` prices a job and names the verification tier
its value implies. ``POST /simulation/submit`` freezes the spec, dispatches it
across registered mesh nodes, compares replicas numerically, and settles only
if they agreed. ``GET /simulation/jobs`` and ``/{id}`` read history;
``GET /simulation/jobs/{id}/evidence`` signs one job into a verifiable bundle.

Two things this refuses to do: settle a job whose replicas disagree, and
charge twice for the same ``idempotency_key``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.decentralized import simulation

router = APIRouter(prefix="/simulation", tags=["simulation"])


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _spec_from_request(req: "SimulationRequest") -> simulation.SimulationSpec:
    try:
        return simulation.SimulationSpec.create(
            kind=req.kind,
            module=req.module,
            params=req.params,
            seed=req.seed,
            steps=req.steps,
            timeout_ms=req.timeout_ms,
            module_digest=req.module_digest,
        )
    except simulation.SimulationError as e:
        raise _bad_request(e)


class SimulationRequest(BaseModel):
    """A job definition. Everything here feeds the frozen spec hash."""

    kind: str = "native"
    module: str
    params: dict[str, Any] = {}
    seed: int = 0
    steps: Optional[int] = None
    timeout_ms: Optional[int] = None
    # sha256 of the Wasm module / model artefact. Pins *what ran*: a swapped
    # binary changes the spec hash, so a replicated result can no longer be
    # compared like-for-like.
    module_digest: str = ""
    replicates: int = 1
    idempotency_key: Optional[str] = None
    # False (default): dispatch and settle inside this request. True: write a
    # QUEUED row and let the durable worker settle it later — survives a
    # restart, returns 202-style shape immediately.
    queued: bool = False

    @validator("module")
    def validate_module(cls, v):
        if not (v or "").strip():
            raise ValueError("module must not be empty")
        return v

    @validator("replicates")
    def validate_replicates(cls, v):
        if v < 1 or v > 32:
            raise ValueError("replicates must be 1..32")
        return v

    @validator("idempotency_key")
    def validate_key(cls, v):
        if v is not None and len(v) > 200:
            raise ValueError("idempotency_key must be 200 characters or fewer")
        return v


def node_status(kind: Optional[str] = None) -> list[dict]:
    """Registered nodes with live health and reputation, worst-first.

    One call answers "can this mesh run my job, and which node should I
    distrust?" — health (can it run now) and reputation (does it agree with
    the rest of the mesh) side by side.
    """
    scores = simulation.reputation()
    out = []
    for node in simulation.available_nodes(kind):
        node_id = node["node_id"]
        entry = scores.get(node_id, {})
        out.append({
            **node,
            "health": simulation.node_health(node_id).to_dict(),
            "reputation": {
                "score": entry.get("score"),
                "agreement_rate": entry.get("agreement_rate"),
                "canary_rate": entry.get("canary_rate"),
                "redundancy_runs": entry.get("redundancy_runs", 0),
                "mismatches": entry.get("mismatches", 0),
                "divergences": entry.get("divergences", 0),
                "trusted": entry.get("trusted", True),
            },
        })
    # Healthy first, then best reputation, then id — the same order the
    # scheduler uses, so the UI shows what dispatch will actually do.
    return sorted(
        out,
        key=lambda n: (
            not n["health"]["available"],
            -(n["reputation"]["score"] if n["reputation"]["score"] is not None else 0.5),
            n["node_id"],
        ),
    )


@router.get("/config")
async def simulation_config():
    """Pricing, tolerances, and the registered node list."""
    return {
        "config": simulation.SimulationConfig.from_env().describe(),
        "kinds": list(simulation.JOB_KINDS),
        "statuses": sorted(simulation.TERMINAL_STATUSES),
        "verdicts": ["MATCH", "MATCH_CONSENSUS", "MISMATCH"],
        "nodes": simulation.available_nodes(),
    }


@router.get("/nodes")
async def simulation_nodes(kind: Optional[str] = None):
    """Fleet health + reputation, in dispatch order."""
    nodes = node_status(kind)
    return {
        "nodes": nodes,
        "count": len(nodes),
        "available": sum(1 for n in nodes if n["health"]["available"]),
        "quarantined": [n["node_id"] for n in nodes if n["health"]["quarantined"]],
        "diverging": [
            n["node_id"] for n in nodes if n["reputation"]["divergences"]
        ],
        "reputation_floor": simulation.REPUTATION_FLOOR,
        "note": (
            "health = can the node run right now (quarantine expires on its "
            "own); reputation = does it agree with the rest of the mesh."
        ),
    }


@router.post("/quote")
async def simulation_quote(req: SimulationRequest):
    """Price a job in DCT/USD and report the verification tier it implies."""
    spec = _spec_from_request(req)
    try:
        return {
            "spec_hash": spec.spec_hash(),
            "spec": spec.to_dict(),
            **simulation.quote(spec, replicates=req.replicates),
            "nodes_available": len(simulation.available_nodes(spec.kind)),
        }
    except simulation.SimulationError as e:
        raise _bad_request(e)


@router.post("/submit")
async def simulation_submit(req: SimulationRequest):
    """Dispatch, verify, and settle (or quarantine) a simulation job."""
    spec = _spec_from_request(req)
    try:
        if req.queued:
            return simulation.enqueue_job(
                spec, replicates=req.replicates,
                idempotency_key=req.idempotency_key,
            )
        return await simulation.submit(
            spec, replicates=req.replicates,
            idempotency_key=req.idempotency_key,
        )
    except simulation.SimulationError as e:
        raise _bad_request(e)


@router.get("/jobs")
async def simulation_jobs(
    limit: int = 50, status: Optional[str] = None, spec_hash: Optional[str] = None,
):
    """Job history, newest first."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be 1..500")
    if status and status not in (simulation.TERMINAL_STATUSES | simulation.OPEN_STATUSES):
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(simulation.TERMINAL_STATUSES | simulation.OPEN_STATUSES)}",
        )
    jobs = simulation.list_jobs(limit=limit, status=status, spec_hash=spec_hash)
    return {"jobs": jobs, "count": len(jobs), "totals": simulation.job_totals()}


@router.get("/jobs/{job_id}")
async def simulation_job(job_id: str):
    """One job with its attempts and numeric verification report."""
    job = simulation.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no simulation job {job_id}")
    return job


@router.get("/jobs/{job_id}/evidence")
async def simulation_job_evidence(job_id: str):
    """Sign one job into an Ed25519 evidence bundle."""
    if simulation.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"no simulation job {job_id}")
    job = simulation.get_job(job_id)
    if job["status"] in simulation.OPEN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job {job_id} is {job['status']}; evidence is only signable for terminal jobs",
        )
    try:
        from backend.decentralized.evidence import save_bundle

        return save_bundle(simulation.simulation_bundle(job_id))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no simulation job {job_id}")