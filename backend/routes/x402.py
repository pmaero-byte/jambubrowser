"""
x402 routes — paywall configuration, receipts, and the anchored root.

The paywall itself lives in ``backend/modules/x402.py`` and is applied as a
dependency on the paid endpoints (``/audit/run``, ``/audit/quick``,
``/dcm/infer``). These routes expose its state so agents and operators can
inspect prices, receipts, and the Merkle root that feeds MeshPay-style
anchoring.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.modules.x402 import (
    X402Config,
    list_receipts,
    receipts_root,
)

router = APIRouter(prefix="/x402", tags=["x402"])


@router.get("/config")
async def x402_config():
    """Paywall settings as clients see them (prices, network, mode)."""
    return X402Config.from_env().describe()


@router.get("/receipts")
async def x402_receipts(limit: int = 50):
    """Recent payment receipts (settled and failed settlements alike)."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be 1..500")
    receipts = list_receipts(limit)
    return {
        "receipts": receipts,
        "count": len(receipts),
        "settled": sum(1 for r in receipts if r["status"] == "settled"),
    }


@router.get("/receipts/root")
async def x402_receipts_root(limit: int = 200):
    """Merkle root over receipt hashes (the payload MeshPay anchors)."""
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=422, detail="limit must be 1..1000")
    return receipts_root(limit)
