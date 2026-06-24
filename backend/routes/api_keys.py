"""API Key management routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional

from backend.core.api_keys import (
    create_api_key,
    validate_api_key,
    list_api_keys,
    deactivate_api_key,
    APIKey,
)

router = APIRouter(tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    name: str
    tier: str = "free"
    owner: str = "default"


class KeyResponse(BaseModel):
    id: int
    key_prefix: str
    name: str
    tier: str
    owner: str
    is_active: bool
    monthly_audit_limit: int
    monthly_audits_used: int


def get_api_key_from_header(x_api_key: Optional[str] = Header(None)) -> Optional[APIKey]:
    if not x_api_key:
        return None
    return validate_api_key(x_api_key)


@router.post("/api-keys/create")
async def api_key_create(req: CreateKeyRequest):
    raw_key, api_key = create_api_key(name=req.name, tier=req.tier, owner=req.owner)
    return {
        "key": raw_key,
        "key_prefix": api_key.key_prefix,
        "name": api_key.name,
        "tier": api_key.tier,
        "monthly_limit": api_key.monthly_audit_limit,
        "message": "Store this key securely — it cannot be retrieved again.",
    }


@router.get("/api-keys/list")
async def api_key_list(owner: str = "default"):
    keys = list_api_keys(owner)
    return {
        "keys": [
            {
                "id": k.id,
                "key_prefix": k.key_prefix + "...",
                "name": k.name,
                "tier": k.tier,
                "is_active": k.is_active,
                "monthly_audit_limit": k.monthly_audit_limit,
                "monthly_audits_used": k.monthly_audits_used,
                "last_used": k.last_used,
            }
            for k in keys
        ]
    }


@router.delete("/api-keys/{key_id}")
async def api_key_delete(key_id: int, owner: str = "default"):
    if deactivate_api_key(key_id, owner):
        return {"status": "deactivated", "key_id": key_id}
    raise HTTPException(status_code=404, detail="Key not found")


@router.get("/api-keys/validate")
async def api_key_validate(x_api_key: Optional[str] = Header(None)):
    key = get_api_key_from_header(x_api_key)
    if not key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return {
        "valid": True,
        "name": key.name,
        "tier": key.tier,
        "monthly_audit_limit": key.monthly_audit_limit,
        "monthly_audits_used": key.monthly_audits_used,
        "remaining": key.monthly_audit_limit - key.monthly_audits_used if key.monthly_audit_limit != -1 else "unlimited",
    }
