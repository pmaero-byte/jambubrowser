"""
Persistence for MeshPay anchor records.

Anchors are the only MeshPay state worth keeping: the receipt data itself
lives on the DCM node (and its own DB), the Merkle root is derivable from
it at any time, and the chain transaction is the durable record. Storing
the record here lets the audit page show anchor history and re-verify that
each anchored root still matches the receipts it claims to cover.
"""

from __future__ import annotations

import json
from typing import Optional

from backend.core.database import get_db


def save_anchor(record: dict) -> dict:
    """Persist an AnchorRecord.to_dict() and return it with its row id."""
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO meshpay_anchors
                (epoch, root, receipts, cluster, transport, signature, memo,
                 epoch_size, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(record["epoch"]),
                str(record["root"]),
                int(record.get("receipts") or 0),
                str(record["cluster"]),
                str(record["transport"]),
                str(record["signature"]),
                str(record["memo"]),
                int(record.get("epoch_size") or 50),
                float(record.get("created_at") or 0),
            ),
        )
        conn.commit()
        anchor_id = cur.lastrowid
    out = dict(record)
    out["id"] = anchor_id
    return out


def list_anchors(limit: int = 50) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meshpay_anchors ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_anchor_for_epoch(epoch: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM meshpay_anchors WHERE epoch = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (epoch,),
        ).fetchone()
    return dict(row) if row else None


def parse_memo(memo: str) -> Optional[dict]:
    """Parse ``meshpay:v1:<epoch>:<receipts>:<root>`` (returns None if foreign)."""
    parts = (memo or "").split(":")
    if len(parts) != 5 or parts[0] != "meshpay" or parts[1] != "v1":
        return None
    try:
        return {
            "epoch": int(parts[2]),
            "receipts": int(parts[3]),
            "root": parts[4],
        }
    except ValueError:
        return None


def re_verify_anchor(anchor: dict, entries: list[dict], *, epoch_size: int) -> dict:
    """Compare an anchored root against the current receipt log.

    The anchor's own ``epoch_size`` wins over the caller's default —
    grouping receipts differently than at anchor time would report a false
    "unavailable".

    Returns the anchor plus ``current_root``/``matches``/``status``:
    - ``verified``  — recomputed root equals the anchored root
    - ``mismatch``  — receipts changed (tamper or a different window)
    - ``unavailable`` — the epoch window isn't in this fetch (truncated)
    """
    from backend.modules.meshpay.plan import group_epochs

    size = int(anchor.get("epoch_size") or epoch_size or 50)
    epochs = group_epochs(entries, epoch_size=size)
    epoch = next((e for e in epochs if e["index"] == anchor["epoch"]), None)
    if epoch is None or epoch["root"] is None:
        return {
            **anchor, "current_root": None, "matches": False,
            "status": "unavailable", "verified_with_epoch_size": size,
        }
    matches = epoch["root"] == anchor["root"]
    return {
        **anchor,
        "current_root": epoch["root"],
        "matches": matches,
        "status": "verified" if matches else "mismatch",
        "verified_with_epoch_size": size,
    }
