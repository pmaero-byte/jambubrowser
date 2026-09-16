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


@router.post("")
async def open_session(req: OpenRequest):
    """Open an isolated browser session for an agent."""
    try:
        session = await get_browser_agent_service().open(
            allow_domains=req.allow_domains,
            require_approval=req.require_approval,
            scrub_pii=req.scrub_pii,
            privacy_level=req.privacy_level,
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


@router.get("/{session_id}/receipts")
async def receipts(session_id: str):
    return _get(session_id).receipts()


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
