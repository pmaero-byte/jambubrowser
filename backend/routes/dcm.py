"""
DecentraCode Mesh (DCM) routes.

Exposes a local DCM node to the browser app and MCP clients: node/mesh
status, model catalog, join info, DCT billing ledger, and mesh inference.
The node itself is configured via ``JAMBU_LLM_DCM_BASE_URL`` (shared with
the ``dcm`` LLM provider) so one env var controls both paths.

Transport notes:
- Every route returns 502 with an actionable message when the node is
  unreachable, so the UI/agent gets "start the node" instead of a hang.
- ``POST /dcm/infer`` runs through the registered ``dcm`` provider, which
  pins DCM's real wire format and error mapping (see
  ``backend/llm/providers/dcm.py``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.llm.base import (
    ChatMessage,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    Role,
)
from backend.llm.config import get_config
from backend.modules.dcm_client import DcmClient, DcmError

router = APIRouter(prefix="/dcm", tags=["dcm"])


def _client() -> DcmClient:
    cfg = get_config()
    return DcmClient(base_url=cfg.dcm_base_url, auth=cfg.dcm_auth)


async def _get(path_coro):
    """Run a DcmClient call, mapping failures to actionable 502s."""
    try:
        return await path_coro
    except DcmError as e:
        detail = (
            f"DCM node unreachable at {get_config().dcm_base_url} — "
            "start it with 'cd decentracode/backend && npm start'"
            if e.status_code == 0
            else f"DCM error: {e.detail}"
        )
        raise HTTPException(status_code=502, detail=detail)


class DcmInferRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    max_tokens: int = 64

    @validator("prompt")
    def validate_prompt(cls, v):
        if not v.strip():
            raise ValueError("prompt must not be empty")
        return v

    @validator("max_tokens")
    def validate_max_tokens(cls, v):
        if v < 1 or v > 4096:
            raise ValueError("max_tokens must be between 1 and 4096")
        return v


@router.get("/status")
async def dcm_status():
    """Node overview: reachable, inference runtimes, model catalog, mesh.

    ``summary()`` internally tolerates per-endpoint failures (it reports
    them in the body), but a broken client/transport still maps through
    ``_get`` for a consistent 502.
    """
    return await _get(_client().summary())


@router.get("/models")
async def dcm_models():
    """DCM's model catalog (id, status, runtime)."""
    models = await _get(_client().models())
    return {"models": models, "count": len(models)}


@router.get("/join-info")
async def dcm_join_info():
    """LAN hosts + peer-page ports for the "Become a node" flow."""
    return await _get(_client().join_info())


@router.get("/earnings/{did}")
async def dcm_earnings(did: str):
    """Accrued DCT for a provider DID."""
    return await _get(_client().earnings(did))


@router.get("/token/balance/{did}")
async def dcm_token_balance(did: str):
    """DCT ledger balance for a DID."""
    return await _get(_client().token_balance(did))


@router.get("/settlement-log")
async def dcm_settlement_log(limit: int = 50):
    """Hash-chained settlement receipts (DCM's auditability primitive)."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be 1..500")
    return await _get(_client().settlement_log(limit=limit))


@router.post("/infer")
async def dcm_infer(req: DcmInferRequest):
    """Run a prompt on the mesh (non-streaming).

    Streaming lives in the ``dcm`` LLM provider; this route exists for
    UI/agent one-shots and returns the completion plus token usage.
    """
    from backend.llm.registry import get_registry

    try:
        provider = get_registry().get("dcm")
        resp = await provider.chat(
            [ChatMessage(role=Role.USER, content=req.prompt)],
            model=req.model,
            max_tokens=req.max_tokens,
        )
    except ProviderAuthError as e:
        raise HTTPException(
            status_code=502,
            detail=f"{e} — set JAMBU_LLM_DCM_AUTH for this node",
        )
    except (ProviderUnavailable, ProviderError) as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "content": resp.content,
        "model": resp.model,
        "finish_reason": resp.finish_reason,
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        },
        "latency_ms": round(resp.latency_ms, 1),
    }


class DcmSimulateRequest(BaseModel):
    """One distributed-solve dispatch (DecentraCode ``/api/simulation/run``).

    ``dofs`` is the TOTAL grid-cell count — the node derives the per-side
    grid as sqrt(dofs). The mesh solves PARAMETRIC catalog problems only
    (no arbitrary geometry); results and DCT billing come back in the body.
    """

    problem_id: str
    dofs: int
    partitions: int = 1
    iterations: Optional[int] = None

    @validator("problem_id")
    def validate_problem_id(cls, v):
        if not v.strip():
            raise ValueError("problem_id must not be empty")
        return v.strip()

    @validator("dofs")
    def validate_dofs(cls, v):
        if v < 1:
            raise ValueError("dofs must be a positive integer")
        return v

    @validator("partitions")
    def validate_partitions(cls, v):
        if v < 1:
            raise ValueError("partitions must be a positive integer")
        return v

    @validator("iterations")
    def validate_iterations(cls, v):
        if v is not None and v < 1:
            raise ValueError("iterations must be a positive integer when set")
        return v


@router.post("/simulate")
async def dcm_simulate(req: DcmSimulateRequest):
    """Dispatch one distributed solve to the mesh and return the result.

    Error mapping mirrors the node's own codes so callers (CFD Lab's
    Debug-stage "Run on mesh", agents) can react precisely: 402 when the
    caller's DCT ledger is short (nothing was solved), 502 when the node is
    unreachable or errors. Non-converged solves are billed only the setup
    fee by the node — the result body says so via ``finalResidual``.
    """
    client = _client()
    try:
        return await client.simulate(
            req.problem_id,
            req.dofs,
            req.partitions,
            req.iterations,
        )
    except DcmError as e:
        if e.status_code == 402:
            raise HTTPException(status_code=402, detail=e.detail)
        detail = (
            f"DCM node unreachable at {get_config().dcm_base_url} — "
            "start it with 'cd decentracode/backend && npm start'"
            if e.status_code == 0
            else f"DCM error: {e.detail}"
        )
        raise HTTPException(status_code=502, detail=detail)


# -- realtime fabric (the lender mesh) ---------------------------------------
#
# The parametric catalog above runs on the node itself. The fabric below
# dispatches a job to a BROWSER LENDER: the node offers it over WebSocket,
# the lender's machine executes the canonical kernel, the coordinator
# re-runs the kernel and verifies the lender's checkpoints numerically, and
# escrow pays the lender's DID. These routes are what CFD Lab's
# ``decentraCompute.ts`` speaks for its "realtime-fabric" lane.


def _fabric_error(e: DcmError) -> HTTPException:
    if e.status_code == 402:
        return HTTPException(status_code=402, detail=e.detail)
    detail = (
        f"DCM node unreachable at {get_config().dcm_base_url} — "
        "start it with 'cd decentracode/backend && npm start'"
        if e.status_code == 0
        else f"DCM error: {e.detail}"
    )
    return HTTPException(status_code=502, detail=detail)


class DcmJobRequest(BaseModel):
    """One realtime-fabric submission (DecentraCode ``POST /api/jobs``).

    Only the ``poisson-cg`` workload exists today. ``dofs`` is TOTAL cells;
    ``n`` may be given instead and the node clamps it to its reference-run
    cap (128). A 402 means the caller's ledger cannot cover the escrow lock
    and NOTHING was submitted.
    """

    workload: str = "poisson-cg"
    dofs: Optional[int] = None
    n: Optional[int] = None
    tol: Optional[float] = None
    max_iter: Optional[int] = None
    max_dct: Optional[float] = None
    timeout_ms: Optional[int] = None

    @validator("workload")
    def validate_workload(cls, v):
        if not v.strip():
            raise ValueError("workload must not be empty")
        return v.strip()

    @validator("dofs", "n")
    def validate_positive_int(cls, v):
        if v is not None and v < 1:
            raise ValueError("must be a positive integer when set")
        return v

    @validator("max_iter", "timeout_ms")
    def validate_positive_optional(cls, v):
        if v is not None and v < 1:
            raise ValueError("must be a positive integer when set")
        return v

    @validator("tol")
    def validate_tol(cls, v):
        if v is not None and v <= 0:
            raise ValueError("tol must be positive when set")
        return v

    @validator("max_dct")
    def validate_max_dct(cls, v):
        if v is not None and v <= 0:
            raise ValueError("max_dct must be positive when set")
        return v

    @validator("n", always=True)
    def validate_has_size(cls, v, values):
        if v is None and values.get("dofs") is None:
            raise ValueError("provide dofs (total cells) or n (per-side grid)")
        return v


@router.get("/realtime/peers")
async def dcm_realtime_peers():
    """Connected browser lenders — the machines that will run a fabric job."""
    return await _get(_client().fabric_peers())


@router.post("/jobs")
async def dcm_submit_job(req: DcmJobRequest):
    """Submit a job to the realtime lender mesh (201 with the job id)."""
    try:
        return await _client().submit_job(
            workload=req.workload,
            dofs=req.dofs,
            n=req.n,
            tol=req.tol,
            max_iter=req.max_iter,
            max_dct=req.max_dct,
            timeout_ms=req.timeout_ms,
        )
    except DcmError as e:
        raise _fabric_error(e)


@router.get("/jobs/{job_id}")
async def dcm_job(job_id: str):
    """Job state, verified checkpoints, the coordinator's verdict and payout.

    A 404 from the node means the coordinator no longer holds the job (its
    journal is in-memory today) — surfaced as 404, not as a 502.
    """
    try:
        return await _client().job(job_id)
    except DcmError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found on the node")
        raise _fabric_error(e)


@router.get("/realtime/settlement/{job_id}")
async def dcm_job_settlement(job_id: str):
    """The escrow pot for one job plus the coordinator's verdict."""
    try:
        return await _client().job_settlement(job_id)
    except DcmError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found on the node")
        raise _fabric_error(e)


class DcmFaucetRequest(BaseModel):
    """Dev funding request. The node refuses this when DID auth is enforced."""

    did: str
    amount: float

    @validator("did")
    def validate_did(cls, v):
        if not v.strip():
            raise ValueError("did must not be empty")
        return v.strip()

    @validator("amount")
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError("amount must be positive")
        return v


@router.post("/realtime/faucet")
async def dcm_realtime_faucet(req: DcmFaucetRequest):
    """Fund a caller DID on the dev node (disabled once auth is enforced)."""
    try:
        return await _client().faucet(req.did, req.amount)
    except DcmError as e:
        if e.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail=f"the node refused the faucet: {e.detail}",
            )
        raise _fabric_error(e)
