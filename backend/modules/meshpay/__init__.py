"""Backward-compatibility shim — moved to backend.decentralized.meshpay."""
from backend.decentralized.meshpay import *  # noqa: F401,F403
from backend.decentralized.meshpay import (  # explicit re-exports
    MeshPayConfig, js_dumps, js_number,
    hash_receipt, invoice_payload, verify_chain,
    merkle_root, leaf_hash,
    build_epoch, payout_plan, group_epochs, receipt_proof,
    AnchorRecord, MockAnchorTransport, SolanaMemoAnchor,
    anchor_root, explorer_url,
    save_anchor, list_anchors, re_verify_anchor,
    payouts,
)
