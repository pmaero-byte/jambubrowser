"""
MeshPay — USDC settlement for the DecentraCode Mesh.

The mesh meters every billable operation (Wasm execution, inference
tokens, simulations) and emits hash-chained receipts. MeshPay makes that
ledger **verifiable and payable**:

- ``jsjson``    — JS-faithful JSON serialization (DCM hashes with
                  ``JSON.stringify``; independent verification requires
                  byte-identical canonical form)
- ``receipts``  — independent replay of DCM's settlement chain
- ``merkle``    — deterministic Merkle root over a receipt window
- ``plan``      — per-epoch provider payout plan (DCT → USDC)
- ``anchor``    — Solana memo-program anchoring (devnet) with an explicit
                  mock transport for tests/demos
- ``store``     — anchored roots persisted for the audit page

Design note: MeshPay never *replaces* DCM's ledger — it audits it and
anchors summaries of it. Settlement amounts are computed from DCM's own
receipts; the DCT→USD rate is configured (not oracle-derived) and every
surface that shows USD says so.
"""

from .config import MeshPayConfig
from .jsjson import js_dumps, js_number
from .receipts import hash_receipt, invoice_payload, verify_chain
from .merkle import merkle_root, leaf_hash
from .plan import build_epoch, payout_plan, group_epochs, receipt_proof
from .anchor import (
    AnchorRecord,
    MockAnchorTransport,
    SolanaMemoAnchor,
    anchor_root,
    explorer_url,
)

__all__ = [
    "MeshPayConfig",
    "js_dumps",
    "js_number",
    "hash_receipt",
    "invoice_payload",
    "verify_chain",
    "merkle_root",
    "leaf_hash",
    "build_epoch",
    "payout_plan",
    "group_epochs",
    "receipt_proof",
    "AnchorRecord",
    "MockAnchorTransport",
    "SolanaMemoAnchor",
    "anchor_root",
    "explorer_url",
]
