"""
VPN control surface.

Read-only by default: ``GET /vpn/status`` reports the resolved configuration
(credentials redacted), the tunnel state, and per-endpoint pool health.
``POST /vpn/select`` resolves which endpoint a given session would use, which
is what the desktop panel and CI callables use to show the current egress.

Bringing a tunnel up or down requires the process to be privileged, so those
actions live on the CLI (``jambu vpn up`` / ``down``) rather than on an HTTP
endpoint an unauthenticated caller could reach.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.vpn import get_vpn_manager
from backend.core.vpn.pool import NoHealthyEndpoint

log = logging.getLogger("jambu.vpn.routes")
router = APIRouter(prefix="/vpn", tags=["vpn"])


class SelectRequest(BaseModel):
    """Ask which endpoint a session would be pinned to."""

    session_key: Optional[str] = Field(
        default=None,
        description="Pin this key to one endpoint for the sticky TTL.",
    )
    exclude: list[str] = Field(
        default_factory=list,
        description="Endpoints to skip (e.g. the one that just failed).",
    )


@router.get("/status")
async def vpn_status():
    """Full VPN status: config (redacted), tunnel state, pool health."""
    manager = get_vpn_manager()
    return await manager.status()


@router.get("/config")
async def vpn_config():
    """Resolved configuration with credentials redacted."""
    return get_vpn_manager().describe()


@router.post("/select")
async def vpn_select(req: SelectRequest):
    """Resolve the endpoint for a session (None means direct connection)."""
    from backend.core.vpn import redact_proxy_url

    manager = get_vpn_manager()
    try:
        proxy = manager.resolve_proxy(req.session_key, exclude=set(req.exclude))
    except NoHealthyEndpoint as exc:
        # Surfaced as 503: the request cannot proceed under the current policy.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "proxy": proxy,
        "redacted_proxy": redact_proxy_url(proxy) if proxy else None,
        "direct": proxy is None,
    }


@router.post("/probe")
async def vpn_probe():
    """Run one health sweep across the pool now."""
    manager = get_vpn_manager()
    result = await manager.pool.probe_once()
    return {"probe": result, "pool": manager.pool.health()}


@router.get("/leak-check")
async def vpn_leak_check(session_key: Optional[str] = None):
    """Prove whether the egress actually carries traffic through the tunnel.

    See ``backend.core.vpn.leakcheck``: compares direct vs proxy IP, IPv6
    routes, and system DNS against the tunnel's resolver.
    """
    from backend.core.vpn.leakcheck import run_leak_check

    manager = get_vpn_manager()
    return await run_leak_check(manager, session_key=session_key)