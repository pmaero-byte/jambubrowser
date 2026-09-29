"""
Decentralized feature gate.

Set ``JAMBU_ENABLE_DECENTRALIZED=1`` to enable the crypto/decentralized
layer (x402 paywall, MeshPay, DCM, P2P, consensus, A2A, verification,
evidence bundles). When disabled, routes return 501 and imports are
lazy no-ops.
"""

import os
import logging

log = logging.getLogger("jambu.decentralized.gate")

_env_flag = os.environ.get("JAMBU_ENABLE_DECENTRALIZED", "").lower()
ENABLED = _env_flag in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    """Return True if the decentralized layer is enabled."""
    return ENABLED


def require_enabled(feature: str):
    """Raise if the decentralized layer is disabled."""
    if not ENABLED:
        raise RuntimeError(
            f"{feature} requires JAMBU_ENABLE_DECENTRALIZED=1 "
            "(decentralized layer is disabled)"
        )
