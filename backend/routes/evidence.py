"""
Evidence routes — create, fetch, verify, and anchor signed evidence bundles.

Bundles are verifiable by a third party with
``scripts/verify_evidence_bundle.py`` (imports nothing from this codebase).
Anchoring writes the bundle's ``payload_hash`` through the MeshPay anchor
module (Solana memo program or the labelled mock transport).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.dcm_client import DcmError
from backend.modules.evidence import (
    audit_report_bundle,
    dcm_settlement_bundle,
    get_bundle,
    list_bundles,
    load_or_create_key,
    record_anchor,
    save_bundle,
    verify_bundle,
    x402_receipts_bundle,
)

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("/key")
async def evidence_key():
    """Public signing identity (fingerprint + public key; never the seed)."""
    key = load_or_create_key()
    return {
        "algorithm": "ed25519",
        "public_key": key.public_hex,
        "fingerprint": key.fingerprint,
    }


@router.post("/audit/{audit_id}")
async def evidence_audit(audit_id: int):
    """Sign a saved audit's findings + envelope."""
    try:
        bundle = audit_report_bundle(audit_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"audit not found or unreadable: {e}")
    return save_bundle(bundle)


@router.post("/x402-receipts")
async def evidence_x402_receipts(limit: int = 200):
    """Sign the x402 receipt window + its Merkle root."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be 1..500")
    return save_bundle(x402_receipts_bundle(limit=limit))


@router.post("/dcm-settlement")
async def evidence_dcm_settlement(limit: int = 200):
    """Sign an independent verification of the DCM settlement receipt chain."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    try:
        bundle = await dcm_settlement_bundle(limit=limit)
    except DcmError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                f"DCM node unreachable — start it with 'cd decentracode/backend && npm start'"
                if e.status_code == 0 else f"DCM error: {e.detail}"
            ),
        )
    return save_bundle(bundle)


@router.get("/bundles")
async def evidence_bundles(limit: int = 50):
    """Bundle history (metadata + anchor status)."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    bundles = list_bundles(limit)
    return {"bundles": bundles, "count": len(bundles)}


@router.get("/bundles/{bundle_id}")
async def evidence_bundle(bundle_id: int):
    """Fetch the full bundle (ready for the standalone verifier)."""
    bundle = get_bundle(bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="bundle not found")
    bundle["verification"] = verify_bundle(bundle)
    return bundle


class VerifyRequest(BaseModel):
    bundle: dict

    def validate_bundle(self):
        if not isinstance(self.bundle, dict) or "signature" not in self.bundle:
            raise ValueError("bundle must be an object with a signature")


@router.post("/verify")
async def evidence_verify(req: VerifyRequest):
    """Verify a posted bundle (convenience; the standalone script is canonical)."""
    return verify_bundle(req.bundle)


class AnchorRequest(BaseModel):
    bundle_id: int


@router.post("/anchor")
async def evidence_anchor(req: AnchorRequest):
    """Anchor a bundle's payload hash (Solana memo / labelled mock)."""
    bundle = get_bundle(req.bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="bundle not found")
    if bundle.get("anchor_signature"):
        raise HTTPException(
            status_code=409,
            detail="bundle already anchored",
        )

    from backend.modules.meshpay import MeshPayConfig, anchor_root, explorer_url

    cfg = MeshPayConfig.from_env()
    try:
        record = await anchor_root(
            root=bundle["payload_hash"],
            epoch=bundle["id"],
            receipts=1,
            cluster=cfg.cluster,
            rpc_url=cfg.rpc_url,
            keypair_path=cfg.keypair or None,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"anchor failed: {e}")

    record_anchor(bundle["id"], record)
    return {
        "bundle_id": bundle["id"],
        "payload_hash": bundle["payload_hash"],
        "signature": record.signature,
        "cluster": record.cluster,
        "transport": record.transport,
        "explorer_url": explorer_url(record.signature, record.cluster),
    }
