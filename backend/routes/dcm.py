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
