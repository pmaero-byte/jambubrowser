"""
Browser session routes — agent-driven browsing with safety rails.

Every session carries an allowlist, optional approval requirement, PII
scrubbing, and a hash-chained receipt log. Refusals are explicit HTTP 403s
with machine-readable reasons (``blocked_domain``, ``approval_required``,
``unsafe_url``, ``unknown_ref``, ``session_limit``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from backend.modules.browser_agent import (
    SessionRefused,
    get_browser_agent_service,
)

router = APIRouter(prefix="/browser/sessions", tags=["browser-sessions"])


def _refusal_to_http(refusal: SessionRefused) -> HTTPException:
    status = 404 if refusal.reason == "not_found" else 429 if refusal.reason == "session_limit" else 403
    return HTTPException(
        status_code=status,
        detail={"reason": refusal.reason, "message": refusal.detail or refusal.reason},
    )


class OpenRequest(BaseModel):
    allow_domains: list[str]
    require_approval: bool = True
    scrub_pii: bool = True
    privacy_level: Optional[str] = None
    allow_private: bool = False

    @validator("allow_domains")
    def validate_domains(cls, v):
        domains = [d.strip().lower() for d in (v or []) if d and d.strip()]
        if not domains:
            raise ValueError("allow_domains must be non-empty (sessions fail closed)")
        return domains


class NavigateRequest(BaseModel):
    url: str


class ActRequest(BaseModel):
    action: str
    ref: str
    text: str = ""
    approve: bool = False


class TakeoverRequest(BaseModel):
    active: bool = True


class RunFlowRequest(BaseModel):
    steps: list[dict]
    approve: bool = False
    stop_on_failure: bool = False
    observe: bool = True
    network: Optional[dict] = None
    resolve_sources: bool = False
    freeze_animations: bool = True


class TestFlowRequest(BaseModel):
    url: str
    steps: list[dict] = []
    allow_domains: list[str] = []
    local: bool = False
    approve: bool = False
    stop_on_failure: bool = False
    privacy_level: Optional[str] = None
    scrub_pii: bool = True
    network: Optional[dict] = None
    resolve_sources: bool = False
    freeze_animations: bool = True
    storage_state: Optional[dict] = None
    context_options: Optional[dict] = None
    trace: bool = False
    har: bool = False
    video: bool = False
    artifacts_dir: Optional[str] = None


class PlanRequest(BaseModel):
    url: str
    goal: str = ""
    kind: Optional[str] = None
    use_llm: bool = False
    provider: str = ""


class SemanticDiffRequest(BaseModel):
    before_elements: list[dict] = []
    after_elements: list[dict] = []
    before_screenshot: Optional[str] = None
    after_screenshot: Optional[str] = None
    use_vision: bool = False
    explain: bool = False


class MatrixRequest(BaseModel):
    url: str
    steps: list[dict] = []
    matrix: Optional[list[dict]] = None
    local: bool = False
    approve: bool = False
    stop_on_failure: bool = False
    network: Optional[dict] = None
    resolve_sources: bool = False
    trace: bool = False
    har: bool = False
    video: bool = False


class ExportRequest(BaseModel):
    steps: list[dict]
    name: str = "jambubrowser flow"
    url: str = ""
    base_url: str = ""
    format: str = "playwright"


@router.post("")
async def open_session(req: OpenRequest):
    """Open an isolated browser session for an agent."""
    try:
        session = await get_browser_agent_service().open(
            allow_domains=req.allow_domains,
            require_approval=req.require_approval,
            scrub_pii=req.scrub_pii,
            privacy_level=req.privacy_level,
            allow_private=req.allow_private,
        )
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)
    return session.info()


@router.get("")
async def list_sessions():
    sessions = get_browser_agent_service().list()
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/{session_id}")
async def session_info(session_id: str):
    try:
        return get_browser_agent_service().get(session_id).info()
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


@router.post("/{session_id}/navigate")
async def navigate(session_id: str, req: NavigateRequest):
    session = _get(session_id)
    try:
        return await session.navigate(req.url)
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


@router.get("/{session_id}/snapshot")
async def snapshot(session_id: str):
    session = _get(session_id)
    try:
        return await session.snapshot()
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


@router.post("/{session_id}/act")
async def act(session_id: str, req: ActRequest):
    session = _get(session_id)
    try:
        return await session.act(
            req.action, req.ref, text=req.text, approve=req.approve,
        )
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


@router.post("/plan")
async def plan_flow(req: PlanRequest):
    """Propose a declarative step flow from a natural-language goal.

    Deterministic template matching by default; ``use_llm=true`` asks the
    configured provider to refine it. Nothing is executed.
    """
    from backend.modules.browser_plan import plan

    return plan(
        req.goal, req.url, kind=req.kind,
        use_llm=req.use_llm, provider=req.provider,
    )


@router.post("/export")
async def export_flow(req: ExportRequest):
    """Export a flow as Playwright Test source or a normalised JSON document."""
    from backend.modules.browser_codegen import flow_to_dict, flow_to_playwright

    if req.format == "json":
        return flow_to_dict(req.steps)
    return {
        "format": "playwright",
        "code": flow_to_playwright(
            req.steps, name=req.name, url=req.url, base_url=req.base_url,
        ),
    }


@router.post("/semantic-diff")
async def semantic_diff(req: SemanticDiffRequest):
    """Human-readable UI diff over element catalogs and/or screenshots."""
    from backend.modules.semantic_diff import (
        explain_diff,
        semantic_diff_elements,
        semantic_diff_images,
    )

    result: dict = {}
    if req.before_elements or req.after_elements:
        result.update(semantic_diff_elements(req.before_elements, req.after_elements))
    if req.before_screenshot and req.after_screenshot:
        result["image"] = await semantic_diff_images(
            req.before_screenshot, req.after_screenshot, use_vision=req.use_vision,
        )
    if req.explain:
        result["explanation"] = explain_diff(result)
    return result


@router.post("/matrix")
async def matrix_flow(req: MatrixRequest):
    """Run the same flow across viewports/locales concurrently."""
    try:
        return await get_browser_agent_service().run_matrix(
            url=req.url, steps=req.steps or None, matrix=req.matrix,
            local=req.local, approve=req.approve, network=req.network,
            stop_on_failure=req.stop_on_failure,
            resolve_sources=req.resolve_sources,
            trace=req.trace, har=req.har, video=req.video,
        )
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


@router.post("/run")
async def test_flow(req: TestFlowRequest):
    """One-shot local test: open → run a declarative flow → close.

    The single-call path an agent uses to test a product: one request covers
    navigation, intent-based interactions, assertions and telemetry.
    """
    try:
        return await get_browser_agent_service().run_test(
            url=req.url,
            steps=req.steps or None,
            allow_domains=req.allow_domains,
            local=req.local,
            approve=req.approve,
            stop_on_failure=req.stop_on_failure,
            privacy_level=req.privacy_level,
            scrub_pii=req.scrub_pii,
            network=req.network,
            resolve_sources=req.resolve_sources,
            freeze_animations=req.freeze_animations,
            storage_state=req.storage_state,
            context_options=req.context_options,
            trace=req.trace,
            har=req.har,
            video=req.video,
            artifacts_dir=req.artifacts_dir,
        )
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


@router.post("/{session_id}/run")
async def run_flow(session_id: str, req: RunFlowRequest):
    """Run a declarative flow against an existing session."""
    session = _get(session_id)
    try:
        return await session.run_flow(
            req.steps, approve=req.approve,
            stop_on_failure=req.stop_on_failure, observe=req.observe,
            network=req.network, resolve_sources=req.resolve_sources,
            freeze_animations=req.freeze_animations,
        )
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


@router.get("/{session_id}/receipts")
async def receipts(session_id: str):
    return _get(session_id).receipts()


@router.get("/{session_id}/screenshot")
async def session_screenshot(session_id: str, full_page: bool = False):
    """Current frame as base64 PNG — the live-view source for human takeover."""
    session = _get(session_id)
    data = await session.capture_screenshot(full_page)
    if data is None:
        raise HTTPException(status_code=501, detail="screenshots unavailable for this session")
    return {
        "session_id": session_id,
        "screenshot_base64": data,
        "human_takeover": session.human_takeover,
    }


@router.post("/{session_id}/takeover")
async def set_takeover(session_id: str, req: TakeoverRequest):
    """Pause/resume agent control for a human (CAPTCHA, 2FA, visual checks).

    Backend state hook — the desktop UI drives the live view.
    """
    session = _get(session_id)
    session.human_takeover = req.active
    return {"session_id": session_id, "human_takeover": session.human_takeover}


@router.post("/{session_id}/evidence")
async def session_evidence(session_id: str):
    """Sign the session's receipt log into a verifiable evidence bundle."""
    from backend.modules.evidence import build_bundle, save_bundle

    session = _get(session_id)
    receipt_log = session.receipts()
    bundle = build_bundle(
        "browser_session",
        {
            "session_id": session.id,
            "allow_domains": session.allow_domains,
            "steps": receipt_log["count"],
            "merkle_root": receipt_log["merkle_root"],
        },
        receipt_log,
    )
    return save_bundle(bundle)


@router.delete("/{session_id}")
async def close_session(session_id: str):
    try:
        return await get_browser_agent_service().close(session_id)
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)


def _get(session_id: str):
    try:
        return get_browser_agent_service().get(session_id)
    except SessionRefused as refusal:
        raise _refusal_to_http(refusal)
