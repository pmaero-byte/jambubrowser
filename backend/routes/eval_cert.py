"""
Agent-evaluation certificate routes.

Run a suite under a frozen spec and get a signed, coverage-checked
certificate; fetch certificates and their verification state; list suites
and their committed task ids.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.modules import eval_cert

router = APIRouter(prefix="/eval", tags=["eval"])


class CertifyRequest(BaseModel):
    suite: str
    provider: Optional[str] = None
    task_ids: Optional[list[str]] = None
    pass_threshold: float = eval_cert.DEFAULT_PASS_THRESHOLD

    @validator("pass_threshold")
    def validate_threshold(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError("pass_threshold must be in [0, 1]")
        return v


@router.get("/suites")
async def list_eval_suites():
    """Available suites with their committed task ids (for the manifest)."""
    import backend.eval.tasks  # noqa: F401 — registers task suites
    from backend.eval.harness import list_suites, list_tasks

    suites = []
    for name in sorted(list_suites()):
        tasks = list_tasks(suite=name)
        suites.append({
            "suite": name,
            "task_count": len(tasks),
            "task_ids": sorted(t.id for t in tasks),
        })
    return {
        "suites": suites,
        "default_pass_threshold": eval_cert.DEFAULT_PASS_THRESHOLD,
    }


@router.post("/certificates")
async def certify(req: CertifyRequest):
    """Run a suite and issue a signed certificate (long-running)."""
    try:
        return await eval_cert.run_and_certify(
            req.suite,
            task_ids=req.task_ids,
            provider=req.provider,
            pass_threshold=req.pass_threshold,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"suite run failed: {e}")


@router.get("/certificates")
async def list_certificates(limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    certificates = eval_cert.list_certificates(limit)
    return {"certificates": certificates, "count": len(certificates)}


@router.get("/certificates/{bundle_id}")
async def get_certificate(bundle_id: int):
    """Full certificate (bundle) plus signature + verdict recomputation."""
    bundle = eval_cert.get_certificate(bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="certificate not found")
    bundle["verification"] = eval_cert.verify_certificate(bundle)
    return bundle
