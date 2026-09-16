"""
A2A routes — agent card plus the JSON-RPC endpoint.

Access: a valid engine API key, an x402 payment (the paywall gates
``POST /a2a`` when enabled), or ``JAMBU_A2A_OPEN=1`` for local development.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.modules.a2a import agent_card, handle_rpc

router = APIRouter(tags=["a2a"])


def _a2a_open() -> bool:
    return (os.environ.get("JAMBU_A2A_OPEN") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _has_api_key(request: Request) -> bool:
    key = request.headers.get("x-api-key")
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        return False
    try:
        from backend.core.api_keys import validate_api_key

        return validate_api_key(key) is not None
    except Exception:
        return False


def _public_base(request: Request) -> str:
    env = (os.environ.get("JAMBU_MCP_PUBLIC_URL") or "").strip().rstrip("/")
    if env:
        return env
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}"


@router.get("/.well-known/agent-card.json")
async def well_known_agent_card(request: Request):
    """Public A2A agent card (discovery)."""
    return agent_card(_public_base(request))


@router.post("/a2a")
async def a2a_rpc(request: Request):
    """JSON-RPC 2.0 endpoint: SendMessage, GetTask, CancelTask."""
    if not _a2a_open() and not _has_api_key(request) and not getattr(
        request.state, "x402_paid", False,
    ):
        raise HTTPException(
            status_code=401,
            detail=(
                "A2A requires auth: send 'Authorization: Bearer <engine API key>', "
                "pay per task via x402 (see GET /x402/config), or set "
                "JAMBU_A2A_OPEN=1 for local development."
            ),
            headers={"WWW-Authenticate": 'Bearer realm="jambubrowser-a2a"'},
        )
    try:
        payload = await request.json()
    except Exception:
        from backend.modules.a2a import A2AError, PARSE_ERROR

        return JSONResponse(A2AError(PARSE_ERROR, "invalid JSON").to_jsonrpc(None))
    if not isinstance(payload, dict):
        from backend.modules.a2a import A2AError, INVALID_REQUEST

        return JSONResponse(A2AError(INVALID_REQUEST, "request must be an object").to_jsonrpc(None))
    return JSONResponse(await handle_rpc(payload))
