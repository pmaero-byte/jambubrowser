"""
Deterministic Merkle tree over receipt hashes (MeshPay's anchor payload).

Spec (canonical — third parties recompute this to audit an anchor):

- Leaves are the ``invoiceHash`` values of an epoch's receipts, in chain
  order, hex-decoded.
- ``leaf_hash(l) = sha256(0x00 || l)``
- ``node_hash(a, b) = sha256(0x01 || a || b)``
- A level with an odd number of nodes **promotes** its last node
  unchanged (Bitcoin-style), which is deterministic and avoids the
  duplicate-last malleability concern.
- An empty receipt set has root ``None`` (nothing to anchor).

The root is what MeshPay writes into a Solana memo transaction; anyone
with the receipt window can recompute the root and compare it to the
on-chain memo.
"""

from __future__ import annotations

import hashlib
from typing import Optional


def leaf_hash(leaf_hex: str) -> bytes:
    """Domain-separated leaf hash for a hex receipt hash."""
    return hashlib.sha256(b"\x00" + bytes.fromhex(leaf_hex)).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def merkle_root(leaf_hexes: list[str]) -> Optional[str]:
    """Merkle root (hex) for a list of receipt hashes; None when empty."""
    if not leaf_hexes:
        return None
    level = [leaf_hash(h) for h in leaf_hexes]
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(_node_hash(level[i], level[i + 1]))
        if len(level) % 2 == 1:
            nxt.append(level[-1])  # promote the odd node
        level = nxt
    return level[0].hex()


def merkle_proof(leaf_hexes: list[str], index: int) -> list[dict]:
    """Inclusion proof for one leaf: [{sibling, position}, ...].

    ``position`` is "left" when the sibling sits left of the running hash.
    Used by the audit page to prove a single receipt belongs to an
    anchored root without fetching the whole epoch.
    """
    if not leaf_hexes or not 0 <= index < len(leaf_hexes):
        raise IndexError("leaf index out of range")
    level = [leaf_hash(h) for h in leaf_hexes]
    running = level[index]
    proof: list[dict] = []
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level) - 1, 2):
            if i == index and index + 1 < len(level):
                proof.append({"sibling": level[i + 1].hex(), "position": "right"})
            elif i + 1 == index:
                proof.append({"sibling": level[i].hex(), "position": "left"})
            nxt.append(_node_hash(level[i], level[i + 1]))
        if len(level) % 2 == 1:
            nxt.append(level[-1])  # promoted: no sibling recorded
        # Track the running node's position in the next level.
        index = index // 2
        level = nxt
    return proof


def verify_proof(leaf_hex: str, proof: list[dict], root_hex: str) -> bool:
    """Recompute a leaf's root from its proof and compare."""
    running = leaf_hash(leaf_hex)
    for step in proof:
        sibling = step.get("sibling")
        if sibling is None:
            continue  # promoted nodes carry over unchanged
        sib = bytes.fromhex(sibling)
        if step.get("position") == "left":
            running = _node_hash(sib, running)
        else:
            running = _node_hash(running, sib)
    return running.hex() == root_hex
