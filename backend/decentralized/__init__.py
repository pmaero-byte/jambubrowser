"""
Decentralized & Crypto Layer
=============================

Specialised modules for decentralised systems, networks, payments,
verifiable compute, and agent-to-agent protocols.

Gate this entire subsystem behind ``JAMBU_ENABLE_DECENTRALIZED=1`` —
see ``backend.decentralized.gate``.
"""

from backend.decentralized.gate import is_enabled

__all__ = ["is_enabled"]
