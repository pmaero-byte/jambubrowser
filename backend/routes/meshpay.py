"""
MeshPay routes — audit, plan, anchor, and prove DCM receipts.

Flow: ``GET /meshpay/audit`` fetches the DCM settlement log, replays the
hash chain **independently**, computes Merkle roots per epoch and a USDC
payout plan. ``POST /meshpay/anchor`` writes an epoch root on-chain (SPL
memo program) or to the explicit mock transport. ``GET /meshpay/anchors``
re-verifies every anchored root against the receipts it claims to cover.

Every payload that mentions USD carries the rate note; every anchor
carries its transport and cluster, so nothing here can be mistaken for an
oracle-priced, mainnet-settled system before it is one.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.modules.dcm_client import DcmClient, DcmError
from backend.modules.meshpay import (
    MeshPayConfig,
    anchor_root,
    explorer_url,
    payout_plan,
    receipt_proof,
    verify_chain,
)
from backend.modules.meshpay.plan import group_epochs
from backend.modules.meshpay.store import re_verify_anchor, save_anchor, list_anchors
from backend.llm.config import get_config as get_llm_config

router = APIRouter(prefix="/meshpay", tags=["meshpay"])

MAX_WINDOW = 200  # DCM caps settlement-log at 200 entries per fetch


def _config() -> MeshPayConfig:
    return MeshPayConfig.from_env()


def _client() -> DcmClient:
    cfg = get_llm_config()
    return DcmClient(base_url=cfg.dcm_base_url, auth=cfg.dcm_auth)


async def _fetch_log(limit: int) -> dict:
    try:
        return await _client().settlement_log(limit=limit)
    except DcmError as e:
        detail = (
            f"DCM node unreachable at {get_llm_config().dcm_base_url} — "
            "start it with 'cd decentracode/backend && npm start'"
            if e.status_code == 0
            else f"DCM error: {e.detail}"
        )
        raise HTTPException(status_code=502, detail=detail)


def _window(limit: int) -> int:
    if limit < 1 or limit > MAX_WINDOW:
        raise HTTPException(
            status_code=422, detail=f"limit must be 1..{MAX_WINDOW}",
        )
    return limit


@router.get("/config")
async def meshpay_config():
    """MeshPay settings as the app sees them (no secrets)."""
    return _config().describe()


@router.get("/audit")
async def meshpay_audit(limit: int = MAX_WINDOW, epoch_size: Optional[int] = None):
    """Independent audit of the DCM receipt chain + epoch plans."""
    _window(limit)
    cfg = _config()
    size = epoch_size or cfg.epoch_size
    if size < 1:
        raise HTTPException(status_code=422, detail="epoch_size must be >= 1")

    log = await _fetch_log(limit)
    entries = log.get("entries") or []
    verdict = verify_chain(entries)
    dcm_verification = log.get("verification") or {}
    epochs = group_epochs(entries, epoch_size=size)

    plan = None
    if epochs:
        plan = payout_plan(
            entries,
            epoch_size=size,
            epoch_index=len(epochs) - 1,
            dct_usd_rate=cfg.dct_usd_rate,
            protocol_fee_pct=cfg.protocol_fee_pct,
        )

    agreement = (
        None if "valid" not in dcm_verification
        else bool(dcm_verification["valid"]) == verdict["valid"]
    )

    return {
        "node_total_entries": log.get("count"),
        "window": {
            "requested": limit,
            "returned": len(entries),
            "truncated": len(entries) < (log.get("count") or 0),
            "first_prev_hash": verdict["first_prev_hash"],
            "head_hash": verdict["head_hash"],
        },
        "verification": verdict,
        "dcm_verification": {
            k: dcm_verification.get(k)
            for k in ("valid", "entries", "brokenAt", "totals")
            if k in dcm_verification
        },
        "agreement": agreement,
        "epochs": [
            {
                "index": e["index"],
                "from_index": e["from_index"],
                "to_index": e["to_index"],
                "receipts": e["receipts"],
                "kinds": e["kinds"],
                "root": e["root"],
                "providers": e["providers"],
            }
            for e in epochs
        ],
        "payout": plan,
        "config": cfg.describe(),
    }


@router.get("/receipts/{position}")
async def meshpay_receipt_proof(position: int, limit: int = MAX_WINDOW,
                                epoch_size: Optional[int] = None):
    """Inclusion proof that receipt #position belongs to its epoch root."""
    _window(limit)
    cfg = _config()
    size = epoch_size or cfg.epoch_size
    if size < 1:
        raise HTTPException(status_code=422, detail="epoch_size must be >= 1")
    entries = (await _fetch_log(limit)).get("entries") or []
    if position < 0 or position >= len(entries):
        raise HTTPException(status_code=404, detail="receipt position out of window")
    proof = receipt_proof(entries, position, epoch_size=size)
    if proof is None:
        raise HTTPException(status_code=404, detail="no proof for this position")
    entry = entries[position]
    return {
        "position": position,
        "kind": entry.get("kind"),
        "invoice_hash": entry.get("invoiceHash"),
        **proof,
    }


class AnchorRequest(BaseModel):
    epoch_index: int = -1          # -1 = latest epoch in the window
    epoch_size: Optional[int] = None
    limit: int = MAX_WINDOW


@router.post("/anchor")
async def meshpay_anchor(req: AnchorRequest):
    """Anchor an epoch's Merkle root (memo program or explicit mock)."""
    _window(req.limit)
    cfg = _config()
    size = req.epoch_size or cfg.epoch_size
    entries = (await _fetch_log(req.limit)).get("entries") or []
    if not entries:
        raise HTTPException(status_code=422, detail="no receipts to anchor")

    epochs = group_epochs(entries, epoch_size=size)
    index = req.epoch_index if req.epoch_index >= 0 else len(epochs) - 1
    if not 0 <= index < len(epochs):
        raise HTTPException(
            status_code=422, detail=f"epoch_index out of range (0..{len(epochs) - 1})",
        )
    epoch = epochs[index]
    if not epoch["root"]:
        raise HTTPException(status_code=422, detail="epoch has no receipt hashes")

    try:
        record = await anchor_root(
            root=epoch["root"],
            epoch=epoch["index"],
            receipts=epoch["receipts"],
            cluster=cfg.cluster,
            rpc_url=cfg.rpc_url,
            keypair_path=cfg.keypair or None,
            epoch_size=size,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"anchor failed: {e}")

    stored = save_anchor(record.to_dict())
    return {
        **stored,
        "explorer_url": explorer_url(record.signature, record.cluster),
        "epoch": {
            "index": epoch["index"],
            "from_index": epoch["from_index"],
            "to_index": epoch["to_index"],
            "receipts": epoch["receipts"],
        },
    }


@router.get("/anchors")
async def meshpay_anchors(limit: int = 20, epoch_size: Optional[int] = None):
    """Anchor history, each re-verified against the current receipt log."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    cfg = _config()
    size = epoch_size or cfg.epoch_size
    anchors = list_anchors(limit)
    entries = (await _fetch_log(MAX_WINDOW)).get("entries") or []
    re_verified = [
        {
            **re_verify_anchor(a, entries, epoch_size=size),
            "explorer_url": explorer_url(a["signature"], a["cluster"]),
        }
        for a in anchors
    ]
    return {
        "anchors": re_verified,
        "count": len(re_verified),
        "verified": sum(1 for a in re_verified if a["status"] == "verified"),
    }


# ---------------------------------------------------------------------------
# Stage 1: wallets, payout batches, approval, execution, reconciliation
# ---------------------------------------------------------------------------

class WalletRequest(BaseModel):
    node_id: str
    wallet_address: str
    source: str = "manual"


class PayoutRequest(BaseModel):
    epoch_index: int = -1
    epoch_size: Optional[int] = None
    limit: int = MAX_WINDOW


@router.get("/wallets")
async def meshpay_wallets():
    """Bound provider wallets (nodeId → Solana address)."""
    from backend.modules.meshpay import payouts

    wallets = payouts.list_wallets()
    return {"wallets": wallets, "count": len(wallets)}


@router.post("/wallets")
async def meshpay_bind_wallet(req: WalletRequest):
    """Bind a provider nodeId to a payout wallet (address is validated)."""
    from backend.modules.meshpay import payouts

    try:
        return payouts.bind_wallet(req.node_id, req.wallet_address, req.source)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/wallets/{node_id}")
async def meshpay_unbind_wallet(node_id: str):
    from backend.modules.meshpay import payouts

    if not payouts.unbind_wallet(node_id):
        raise HTTPException(status_code=404, detail="wallet binding not found")
    return {"node_id": node_id, "unbound": True}


@router.post("/payouts")
async def meshpay_create_payout(req: PayoutRequest):
    """Plan an epoch's payout: instructions for bound wallets, unbound reported."""
    _window(req.limit)
    from backend.modules.meshpay import payouts

    cfg = _config()
    size = req.epoch_size or cfg.epoch_size
    if size < 1:
        raise HTTPException(status_code=422, detail="epoch_size must be >= 1")
    entries = (await _fetch_log(req.limit)).get("entries") or []
    try:
        batch = payouts.build_payout_batch(
            entries, epoch_index=req.epoch_index, epoch_size=size,
            dct_usd_rate=cfg.dct_usd_rate, protocol_fee_pct=cfg.protocol_fee_pct,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return payouts.save_batch(batch)


@router.get("/payouts")
async def meshpay_list_payouts(limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    from backend.modules.meshpay import payouts

    batches = payouts.list_batches(limit)
    return {"payouts": batches, "count": len(batches)}


@router.get("/payouts/{batch_id}")
async def meshpay_get_payout(batch_id: int):
    from backend.modules.meshpay import payouts

    batch = payouts.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="payout batch not found")
    return batch


@router.post("/payouts/{batch_id}/approve")
async def meshpay_approve_payout(batch_id: int, request: Request):
    """Operator approval — requires JAMBU_ADMIN_API_KEY (fail closed)."""
    from backend.modules.meshpay import payouts

    admin_key = request.headers.get("x-admin-api-key")
    try:
        return payouts.approve_batch(batch_id, admin_key)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/payouts/{batch_id}/execute")
async def meshpay_execute_payout(batch_id: int):
    """Prepare (mock/no keypair) or broadcast (real cluster + treasury key)."""
    from backend.modules.meshpay import payouts

    try:
        return payouts.execute_batch(batch_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"payout execution failed: {e}")


@router.get("/payouts/{batch_id}/reconcile")
async def meshpay_reconcile_payout(batch_id: int):
    """Re-check a batch against the live receipt window + anchor log."""
    from backend.modules.meshpay import payouts

    entries = (await _fetch_log(MAX_WINDOW)).get("entries") or []
    try:
        return payouts.reconcile_batch(batch_id, entries)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
