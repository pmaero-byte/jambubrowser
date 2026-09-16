"""
Independent replay of DCM's settlement receipt chain.

DCM (``backend/models/billingEngine.js``) appends receipts of the form::

    { kind, timestamp, prevInvoiceHash, ...receipt, invoiceHash }

where ``invoiceHash = sha256(JSON.stringify(entry without invoiceHash))``
and ``prevInvoiceHash`` is the previous entry's ``invoiceHash`` (``null``
for the first entry). ``verifySettlementLog()`` replays exactly that.

This module re-implements the replay **independently** (own serializer, own
aggregation) so that a MeshPay verdict is evidence about DCM, not a mirror
of DCM's own answer. Callers should compare the two.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from .jsjson import js_dumps


def hash_receipt(payload: dict) -> str:
    """sha256 hex over the JS-canonical serialization of a receipt payload."""
    return hashlib.sha256(js_dumps(payload).encode("utf-8")).hexdigest()


def invoice_payload(entry: dict) -> dict:
    """The hashed portion of an entry: everything except ``invoiceHash``."""
    return {k: v for k, v in entry.items() if k != "invoiceHash"}


def verify_chain(entries: list[dict]) -> dict:
    """Replay a chain window and recompute its economics.

    Returns::

        {
          "valid": bool,               # every link + hash matches
          "checked": int,              # entries hash-verified
          "broken_at": int | None,     # index of the first bad entry
          "broken_reason": str | None,
          "first_prev_hash": str|None, # window start; None = genesis
          "head_hash": str | None,     # last invoiceHash (chain head)
          "window_truncated": bool,    # start isn't the chain's genesis
          "totals": {...},             # same aggregates DCM reports
          "account_deltas": {...},     # did/nodeId -> delta
          "kinds": {...},              # kind -> count
        }

    A windowed fetch (DCM caps ``settlement-log`` at 200 entries) verifies
    links *within* the window: the first entry's ``prevInvoiceHash`` cannot
    be checked against a predecessor that isn't fetched, so it is reported
    via ``first_prev_hash`` and ``window_truncated`` instead.
    """
    prev_hash: Optional[str] = None
    valid = True
    broken_at: Optional[int] = None
    broken_reason: Optional[str] = None
    totals = {
        "totalMinted": 0.0,
        "totalCommission": 0.0,
        "totalBurned": 0.0,
        "totalCharged": 0.0,
    }
    account_deltas: dict[str, float] = {}
    kinds: dict[str, int] = {}
    first_prev_hash: Optional[str] = None
    head_hash: Optional[str] = None

    for index, entry in enumerate(entries):
        if index == 0:
            # A window (DCM caps settlement-log at 200 entries) starts
            # mid-chain: its first link points at an entry we don't have.
            # Trust it as the window's root and report truncation instead.
            first_prev_hash = entry.get("prevInvoiceHash")
            prev_hash = first_prev_hash
        kind = str(entry.get("kind", "?"))
        kinds[kind] = kinds.get(kind, 0) + 1

        expected = hash_receipt(invoice_payload(entry))
        stored = entry.get("invoiceHash")
        if stored != expected:
            valid = False
            broken_at = index
            broken_reason = (
                f"invoice hash mismatch: expected {expected[:16]}…, got {str(stored)[:16]}…"
            )
            break
        if entry.get("prevInvoiceHash") != prev_hash:
            valid = False
            broken_at = index
            broken_reason = (
                f"chain link broken: prevInvoiceHash {str(entry.get('prevInvoiceHash'))[:16]}… "
                f"≠ previous head {str(prev_hash)[:16]}…"
            )
            break

        prev_hash = stored
        head_hash = stored

        # Economics — mirrors DCM's aggregation over known kinds.
        if kind == "settlement":
            net = float(entry.get("netReward") or 0)
            commission = float(entry.get("protocolCommission") or 0)
            burned = float((entry.get("treasuryDistribution") or {}).get("burned") or 0)
            totals["totalMinted"] += net
            totals["totalCommission"] += commission
            totals["totalBurned"] += burned
            node = entry.get("nodeId")
            if node:
                account_deltas[node] = account_deltas.get(node, 0.0) + net
        elif kind in ("inference-charge", "simulation-charge"):
            charged = float(entry.get("chargedDct") or 0)
            totals["totalCharged"] += charged
            if entry.get("balanceAfter") is not None:
                did = entry.get("did")
                if did:
                    account_deltas[did] = account_deltas.get(did, 0.0) - charged
        # 'usage' accrues provider earnings; 'dense-receipt' is a pure
        # proof anchor. Neither changes ledger balances.

    return {
        "valid": valid,
        "checked": len(entries) if broken_at is None else broken_at,
        "broken_at": broken_at,
        "broken_reason": broken_reason,
        "first_prev_hash": first_prev_hash,
        "head_hash": head_hash,
        "window_truncated": first_prev_hash is not None,
        "totals": {k: round(v, 9) for k, v in totals.items()},
        "account_deltas": {k: round(v, 9) for k, v in account_deltas.items()},
        "kinds": kinds,
    }
