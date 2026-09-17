"""
Verification routes — policy, redundant execution, canaries, scorecards.

See ``backend/modules/verification.py`` for the tier model. The evidence
endpoint signs the verdict window into a ``compute_verification`` bundle
(verifiable with ``scripts/verify_evidence_bundle.py``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.modules import verification

router = APIRouter(prefix="/verification", tags=["verification"])


class RedundantRequest(BaseModel):
    text: str
    primary: str = "echo"
    replica: str = "echo"
    comparator: str = "similarity"
    tolerance: float = verification.DEFAULT_TOLERANCE

    @validator("text")
    def validate_text(cls, v):
        if not (v or "").strip():
            raise ValueError("text must not be empty")
        return v

    @validator("tolerance")
    def validate_tolerance(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError("tolerance must be in [0, 1]")
        return v


class CanaryRequest(BaseModel):
    worker_id: str = "echo"


@router.get("/policy")
async def get_policy():
    """Tier model + thresholds (declared, not enforcement)."""
    return verification.policy()


@router.get("/workers")
async def list_workers(worker_id: Optional[str] = None):
    """Scorecards: canary pass rate + redundancy agreement per worker."""
    return verification.worker_report(worker_id)


@router.get("/verdicts")
async def list_verdicts(limit: int = 50, worker_id: Optional[str] = None):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be 1..500")
    verdicts = verification.list_verdicts(limit=limit, worker_id=worker_id)
    return {"verdicts": verdicts, "count": len(verdicts)}


@router.post("/redundant")
async def run_redundant(req: RedundantRequest):
    """Run the same task on two executors and compare under a tolerance."""
    try:
        return await verification.run_redundant(
            req.text, primary=req.primary, replica=req.replica,
            comparator=req.comparator, tolerance=req.tolerance,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/canary")
async def run_canary(req: CanaryRequest):
    """Known-answer probes against one worker."""
    result = await verification.run_canary(req.worker_id)
    if result["canaries"] and result["canaries"][0].get("error"):
        raise HTTPException(
            status_code=422, detail=result["canaries"][0]["error"],
        )
    return result


@router.post("/evidence")
async def verification_evidence(limit: int = 200):
    """Sign the verdict window into an evidence bundle."""
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=422, detail="limit must be 1..1000")
    from backend.modules.evidence import save_bundle

    return save_bundle(verification.verification_bundle(limit=limit))
