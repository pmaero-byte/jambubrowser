"""
QA routes — managed test cases for the AI QA team (Milestone 1).

- CRUD over cases (goal + steps + severity + owner)
- ``POST /qa/from-goal``: NL goal → template plan → stored case (one call)
- ``POST /qa/cases/{id}/run``: verify → heal → retry, persisted verdict
- heal events: list + accept/reject (accept is the only step-mutator)
- per-case stats (pass rate + heal rate) for the dashboard feed
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from backend.core.security import is_safe_url
from backend.modules import qa_cases

router = APIRouter(prefix="/qa", tags=["qa"])


def _check_url(url: str, local: bool) -> str:
    if not is_safe_url(url, allow_private=local):
        raise ValueError(
            "Invalid or blocked URL (local URLs need local=true)")
    return url


class CaseCreate(BaseModel):
    name: str
    url: str
    steps: list[dict]
    goal: str = ""
    kind: str = "smoke"
    severity: str = "medium"
    owner: str = ""
    enabled: bool = True
    local: bool = False
    dataset_id: Optional[int] = None

    @model_validator(mode="after")
    def _validate_url(self):
        self.url = _check_url(self.url, self.local)
        return self


class CaseUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    goal: Optional[str] = None
    kind: Optional[str] = None
    steps: Optional[list[dict]] = None
    severity: Optional[str] = None
    owner: Optional[str] = None
    enabled: Optional[bool] = None
    local: Optional[bool] = None
    dataset_id: Optional[int] = None

    @model_validator(mode="after")
    def _validate_url(self):
        if self.url is not None and not (
                is_safe_url(self.url)
                or is_safe_url(self.url, allow_private=True)):
            raise ValueError("Invalid or blocked URL")
        return self


class FromGoalRequest(BaseModel):
    name: str
    url: str
    goal: str = ""
    kind: Optional[str] = None
    use_llm: bool = False
    provider: str = ""
    severity: str = "medium"
    owner: str = ""
    local: bool = False
    dataset_id: Optional[int] = None

    @model_validator(mode="after")
    def _validate_url(self):
        self.url = _check_url(self.url, self.local)
        return self


class RunRequest(BaseModel):
    local: bool = False
    approve: bool = False
    stop_on_failure: bool = False
    trace: bool = False
    har: bool = False
    video: bool = False
    dataset_rows: Optional[list[dict]] = None
    viewport_matrix: Optional[list[dict]] = None
    junit: bool = False
    force: bool = False


class HealDecideRequest(BaseModel):
    accept: bool = True
    actor: str = "qa-lead"


class QuarantineRequest(BaseModel):
    reason: str = ""
    actor: str = "qa-lead"


class AutoRetryRequest(BaseModel):
    enabled: bool = True


@router.post("/cases")
def create_case(req: CaseCreate):
    try:
        return qa_cases.create_case(
            req.name, req.url, req.steps, goal=req.goal, kind=req.kind,
            severity=req.severity, owner=req.owner, enabled=req.enabled,
            dataset_id=req.dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/cases/{case_id}")
def update_case(case_id: int, req: CaseUpdate):
    try:
        case = qa_cases.update_case(
            case_id, **req.dict(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if case is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    return case


@router.delete("/cases/{case_id}")
def delete_case(case_id: int):
    if not qa_cases.delete_case(case_id):
        raise HTTPException(status_code=404, detail="QA case not found")
    return {"case_id": case_id, "deleted": True}

@router.get("/cases")
def list_cases(enabled_only: bool = False):
    cases = qa_cases.list_cases(enabled_only=enabled_only)
    return {"cases": cases, "count": len(cases)}


@router.get("/cases/{case_id}")
def get_case(case_id: int):
    case = qa_cases.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    case["stats"] = qa_cases.case_stats(case_id)
    return case



@router.post("/from-goal")
def from_goal(req: FromGoalRequest):
    """NL goal → template plan → stored case. The demo path."""
    from backend.modules.browser_plan import plan

    proposal = plan(req.goal or req.kind or "smoke test", req.url,
                    kind=req.kind, use_llm=req.use_llm,
                    provider=req.provider)
    steps = proposal.get("steps") or []
    if not steps:
        raise HTTPException(status_code=400,
                            detail="planner produced no steps")
    try:
        case = qa_cases.create_case(
            req.name, req.url, steps, goal=req.goal or proposal.get("goal",
                                                                   ""),
            kind=proposal.get("kind") or req.kind or "smoke",
            severity=req.severity, owner=req.owner,
            dataset_id=req.dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    case["plan"] = {k: proposal.get(k) for k in
                    ("kind", "matched", "placeholders", "source", "notes")}
    return case


@router.post("/cases/{case_id}/run")
async def run_case(case_id: int, req: RunRequest):
    try:
        return await qa_cases.run_case(
            case_id, local=req.local, approve=req.approve,
            stop_on_failure=req.stop_on_failure, trace=req.trace,
            har=req.har, video=req.video,
            dataset_rows=req.dataset_rows, junit=req.junit,
            viewport_matrix=req.viewport_matrix,
            force=req.force)
    except ValueError as exc:
        raise HTTPException(status_code=409 if "quarantined" in str(exc)
                            else 404, detail=str(exc))


@router.get("/cases/{case_id}/runs")
def list_runs(case_id: int, limit: int = 20):
    if qa_cases.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    runs = qa_cases.list_runs(case_id, limit=limit)
    return {"case_id": case_id, "runs": runs, "count": len(runs)}


@router.get("/cases/{case_id}/junit")
def case_junit(case_id: int, limit: int = 20):
    """JUnit XML for the recent runs of a case (CI gate artifact)."""
    from backend.modules.qa_datasets import runs_to_junit
    from fastapi.responses import PlainTextResponse

    case = qa_cases.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    runs = qa_cases.list_runs(case_id, limit=limit)
    xml = runs_to_junit(case["name"], runs)
    return PlainTextResponse(xml, media_type="application/xml")


@router.get("/cases/{case_id}/stats")
def stats(case_id: int, window: int = 20):
    if qa_cases.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    return qa_cases.case_stats(case_id, window=window)


@router.get("/overview")
def overview(window: int = 20):
    """Dashboard feed: every case + its pass/heal/flake stats in one call."""
    cases = qa_cases.list_cases()
    entries = []
    for case in cases:
        entry = {**case, "stats": qa_cases.case_stats(case["id"],
                                                      window=window)}
        entries.append(entry)
    quarantined = sum(1 for e in entries if e["stats"].get("quarantined"))
    with_runs = [e for e in entries if e["stats"].get("runs")]
    return {
        "cases": entries,
        "count": len(entries),
        "summary": {
            "cases": len(entries),
            "quarantined": quarantined,
            "pass_rate": (
                round(sum(e["stats"]["pass_rate"] for e in with_runs)
                      / len(with_runs), 4) if with_runs else None),
            "flake_rate": (
                round(sum(e["stats"]["flake_rate"] for e in with_runs)
                      / len(with_runs), 4) if with_runs else None),
            "heal_rate": (
                round(sum(e["stats"]["heal_rate"] for e in with_runs)
                      / len(with_runs), 4) if with_runs else None),
        },
    }


@router.get("/heals")

@router.get("/cases/{case_id}/sarif")
def case_sarif(case_id: int, limit: int = 20):
    """SARIF 2.1.0 for a case's recent runs (GitHub code scanning)."""
    from backend.modules.qa_datasets import runs_to_sarif
    from fastapi.responses import JSONResponse

    case = qa_cases.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    runs = qa_cases.list_runs(case_id, limit=limit)
    sarif = runs_to_sarif(case["name"], runs,
                          case_severity=case["severity"], url=case["url"])
    return JSONResponse(sarif, media_type="application/sarif+json")


@router.post("/cases/{case_id}/quarantine")
def quarantine(case_id: int, req: QuarantineRequest):
    case = qa_cases.quarantine_case(case_id, reason=req.reason,
                                    actor=req.actor)
    if case is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    return case


@router.post("/cases/{case_id}/unquarantine")
def unquarantine(case_id: int, req: QuarantineRequest):
    case = qa_cases.unquarantine_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    return case


@router.post("/cases/{case_id}/auto-retry")
def auto_retry(case_id: int, req: AutoRetryRequest):
    case = qa_cases.set_auto_retry(case_id, req.enabled)
    if case is None:
        raise HTTPException(status_code=404, detail="QA case not found")
    return case



# ── datasets ──────────────────────────────────────────────────────────

class DatasetCreate(BaseModel):
    name: str
    rows: list[dict]


@router.post("/datasets")
def create_dataset(req: DatasetCreate):
    from backend.modules import qa_datasets

    try:
        return qa_datasets.create_dataset(req.name, req.rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/datasets")
def list_datasets():
    from backend.modules import qa_datasets

    datasets = qa_datasets.list_datasets()
    return {"datasets": datasets, "count": len(datasets)}


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: int):
    from backend.modules import qa_datasets

    dataset = qa_datasets.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    preview = qa_datasets.dataset_preview(dataset_id)
    dataset["preview_rows"] = preview["rows"]
    dataset["columns"] = preview["columns"]
    dataset["row_count"] = preview["row_count"]
    return dataset


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: int):
    from backend.modules import qa_datasets

    if not qa_datasets.delete_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    return {"dataset_id": dataset_id, "deleted": True}


def list_heals(case_id: Optional[int] = None,
               status: Optional[str] = None, limit: int = 50):
    return {"heals": qa_cases.list_heal_events(case_id, status=status,
                                               limit=limit)}


@router.post("/heals/{event_id}")
def decide_heal(event_id: int, req: HealDecideRequest):
    event = qa_cases.decide_heal_event(event_id, accept=req.accept,
                                       actor=req.actor)
    if event is None:
        raise HTTPException(status_code=404, detail="heal event not found")
    return event
