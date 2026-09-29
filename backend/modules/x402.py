"""Backward-compatibility shim — moved to backend.decentralized.x402."""
from backend.decentralized.x402 import *  # noqa: F401,F403
from backend.decentralized.x402 import (  # explicit re-exports
    X402Middleware, DEFAULT_PAID_ROUTES,
    _claim_nonce, _release_nonce,
    list_receipts, receipts_root,
)
