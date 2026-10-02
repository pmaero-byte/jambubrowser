"""
Epoch grouping and USDC payout planning.

An **epoch** is a contiguous window of settlement receipts (by chain
position). For each epoch MeshPay computes the Merkle root (the anchor
payload) and a payout plan.

Payout rules (deliberately simple and auditable):

- Provider entitlement comes from **any receipt that names a provider and
  carries a positive ``reward``**. This used to be keyed on ``kind ==
  "usage"`` only, which silently paid simulation providers nothing: the mesh
  meters simulation work under its own receipt kinds, so a node that only
  ever ran simulations never appeared in the plan and was never paid. DCM's
  naming of those kinds is not guaranteed to stay stable, so entitlement is
  derived from the receipt *shape* instead.
- ``settlement`` receipts are shown separately as *already settled on DCM*
  and are **not** added into the plan, otherwise usage + settlement in the
  same epoch would double-count. ``dense-receipt`` is a pure proof anchor
  and pays nothing either.
- MeshPay applies its protocol fee on top (``protocol_fee_pct``) and
  converts at a **configured** rate (``dct_usd_rate``). The rate is not an
  oracle; every payload carrying USD says so explicitly.

This is the "payment oracle" shape from the MeshPay design: DCM's receipt
log is the source of truth, MeshPay is a deterministic function of it.
"""

from __future__ import annotations

from typing import Any, Optional

from .merkle import merkle_root, merkle_proof
DEFAULT_EPOCH_SIZE = 50

#: Kinds that must never contribute provider entitlement — ``settlement``
#: already paid out on DCM (adding it would double-count) and
#: ``dense-receipt`` is a proof anchor, not a payment.
NON_PAYING_KINDS = frozenset({"settlement", "dense-receipt"})

#: Consumer-side debit kinds, split out per kind for reconciliation.
CHARGE_KINDS = ("inference-charge", "simulation-charge")

#: Field names DCM receipts use to correlate a charge with the provider work
#: that earned from it. Checked in order; first hit wins.
CORRELATION_FIELDS = ("executionHash", "jobId", "execHash", "simulationId")


def _correlation_id(entry: dict) -> Optional[str]:
    """The id tying a charge receipt to the provider work it paid for."""
    for field in CORRELATION_FIELDS:
        value = entry.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def _reward_of(entry: dict) -> float:
    try:
        return float(entry.get("reward") or 0)
    except (TypeError, ValueError):
        return 0.0


def is_provider_reward(entry: dict) -> bool:
    """True when this receipt pays a provider.

    Shape-based on purpose: a receipt pays out when it names a ``nodeId`` and
    carries a positive ``reward``, whatever DCM decided to call its ``kind``.
    """
    if str(entry.get("kind", "")) in NON_PAYING_KINDS:
        return False
    if not entry.get("nodeId"):
        return False
    return _reward_of(entry) > 0


def reconcile_window(window: list[dict]) -> dict:
    """Reconcile consumer charges against the provider rewards they paid for.

    A ``simulation-charge`` is the *buyer's* debit. The provider's cut only
    exists if some reward receipt names the same work. Comparing the two is
    the only way to notice simulation work that was charged but never paid —
    the exact failure that made simulation providers earn $0.

    Correlation is best-effort: receipts are linked by a shared
    ``executionHash``/``jobId``-style field when DCM supplies one. When it
    does not, the result is reported as ``correlated: false`` and only
    aggregate totals are trustworthy — this never claims a per-job match it
    cannot actually make.
    """
    by_kind: dict[str, dict[str, float]] = {}
    charged_ids: set[str] = set()
    reward_ids: set[str] = set()
    provider_reward = 0.0

    for entry in window:
        kind = str(entry.get("kind", ""))
        if kind in CHARGE_KINDS:
            try:
                charged = float(entry.get("chargedDct") or 0)
            except (TypeError, ValueError):
                charged = 0.0
            bucket = by_kind.setdefault(kind, {"jobs": 0, "chargedDct": 0.0})
            bucket["jobs"] += 1
            bucket["chargedDct"] += charged
            correlation = _correlation_id(entry)
            if correlation:
                charged_ids.add(correlation)
        elif is_provider_reward(entry):
            provider_reward += _reward_of(entry)
            correlation = _correlation_id(entry)
            if correlation:
                reward_ids.add(correlation)

    for bucket in by_kind.values():
        bucket["chargedDct"] = round(bucket["chargedDct"], 9)

    correlated = bool(charged_ids or reward_ids)
    unmatched = sorted(charged_ids - reward_ids) if correlated else []
    total_charged = round(sum(b["chargedDct"] for b in by_kind.values()), 9)

    result: dict[str, Any] = {
        "by_kind": by_kind,
        "totalChargedDct": total_charged,
        "providerRewardDct": round(provider_reward, 9),
        "correlated": correlated,
        "unmatchedChargeIds": unmatched[:50],
        "unmatchedChargeCount": len(unmatched),
    }
    if not correlated:
        result["note"] = (
            "No correlation id on these receipts, so charged-but-unpaid work "
            "cannot be attributed per job; only the aggregate totals hold."
        )
    elif unmatched:
        result["note"] = (
            f"{len(unmatched)} charge(s) in this window have no matching "
            "provider reward receipt — work that was billed but not paid."
        )
    return result


def build_epoch(
    entries: list[dict], *, index: int, from_index: int, to_index: int,
) -> dict:
    """Summarize one epoch window (entries[from_index:to_index+1])."""
    window = entries[from_index : to_index + 1]
    invoice_hashes = [
        e.get("invoiceHash") for e in window if e.get("invoiceHash")
    ]
    kinds: dict[str, int] = {}
    for e in window:
        kind = str(e.get("kind", "?"))
        kinds[kind] = kinds.get(kind, 0) + 1
    providers: dict[str, dict[str, Any]] = {}
    settled: dict[str, float] = {}
    for offset, e in enumerate(window):
        if str(e.get("kind", "")) == "settlement":
            node = e.get("nodeId") or "unknown"
            settled[node] = settled.get(node, 0.0) + float(e.get("netReward") or 0)
            continue
        if not is_provider_reward(e):
            continue
        node = e["nodeId"]
        entry = providers.setdefault(
            node,
            {
                "nodeId": node, "accruedDct": 0.0, "receipts": 0,
                "root_indices": [], "rewardByKind": {},
            },
        )
        reward = _reward_of(e)
        entry["accruedDct"] += reward
        entry["receipts"] += 1
        entry["root_indices"].append(from_index + offset)
        kind = str(e.get("kind", "?"))
        entry["rewardByKind"][kind] = round(
            entry["rewardByKind"].get(kind, 0.0) + reward, 9,
        )

    for entry in providers.values():
        entry["accruedDct"] = round(entry["accruedDct"], 9)

    return {
        "index": index,
        "from_index": from_index,
        "to_index": to_index,
        "receipts": len(window),
        "kinds": kinds,
        "root": merkle_root(invoice_hashes),
        "head_hash": invoice_hashes[-1] if invoice_hashes else None,
        "first_prev_hash": window[0].get("prevInvoiceHash") if window else None,
        "providers": sorted(
            providers.values(), key=lambda p: p["accruedDct"], reverse=True,
        ),
        "already_settled_dct": {
            k: round(v, 9) for k, v in settled.items()
        },
        "metering": reconcile_window(window),
    }


def group_epochs(
    entries: list[dict], *, epoch_size: int = DEFAULT_EPOCH_SIZE,
) -> list[dict]:
    """Split a receipt window into contiguous epochs."""
    if epoch_size < 1:
        raise ValueError("epoch_size must be >= 1")
    epochs = []
    for start in range(0, len(entries), epoch_size):
        end = min(start + epoch_size, len(entries)) - 1
        epochs.append(
            build_epoch(entries, index=len(epochs), from_index=start, to_index=end)
        )
    return epochs


def payout_plan(
    entries: list[dict],
    *,
    epoch_size: int = DEFAULT_EPOCH_SIZE,
    epoch_index: int = 0,
    dct_usd_rate: float = 0.01,
    protocol_fee_pct: float = 0.15,
) -> dict:
    """USDC payout plan for one epoch.

    Amounts: ``grossDct`` (entitlement) → ``feeDct`` → ``netDct`` → ``usdc``.
    All USD figures include ``rate_note`` so no surface can present them as
    oracle-derived.
    """
    if not 0.0 <= protocol_fee_pct < 1.0:
        raise ValueError("protocol_fee_pct must be in [0, 1)")
    if dct_usd_rate < 0:
        raise ValueError("dct_usd_rate must be >= 0")

    epochs = group_epochs(entries, epoch_size=epoch_size)
    if not epochs:
        raise ValueError("no receipts to plan")
    if not 0 <= epoch_index < len(epochs):
        raise ValueError(f"epoch_index out of range (0..{len(epochs) - 1})")
    epoch = epochs[epoch_index]

    providers = []
    total_gross = 0.0
    for p in epoch["providers"]:
        gross = p["accruedDct"]
        fee = gross * protocol_fee_pct
        net = gross - fee
        total_gross += gross
        providers.append({
            "nodeId": p["nodeId"],
            "receipts": p["receipts"],
            # What this provider earned per receipt kind, so simulation work
            # is visible as its own line instead of vanishing into "usage".
            "rewardByKind": p.get("rewardByKind", {}),
            "grossDct": round(gross, 9),
            "feeDct": round(fee, 9),
            "netDct": round(net, 9),
            "usdc": round(net * dct_usd_rate, 6),
        })

    total_fee = total_gross * protocol_fee_pct
    total_net = total_gross - total_fee
    return {
        "epoch": {
            "index": epoch["index"],
            "from_index": epoch["from_index"],
            "to_index": epoch["to_index"],
            "receipts": epoch["receipts"],
            "root": epoch["root"],
            "head_hash": epoch["head_hash"],
        },
        "dct_usd_rate": dct_usd_rate,
        "protocol_fee_pct": protocol_fee_pct,
        "rate_note": (
            "DCT→USD rate and fee are MeshPay configuration, not an oracle; "
            "DCT remains an internal ledger."
        ),
        "providers": providers,
        "already_settled_dct": epoch["already_settled_dct"],
        "metering": epoch["metering"],
        "totals": {
            "grossDct": round(total_gross, 9),
            "feeDct": round(total_fee, 9),
            "netDct": round(total_net, 9),
            "usdc": round(total_net * dct_usd_rate, 6),
        },
    }


def receipt_proof(
    entries: list[dict], position: int, *, epoch_size: int = DEFAULT_EPOCH_SIZE,
) -> Optional[dict]:
    """Inclusion proof for one receipt in its epoch ("show me this receipt
    is in the anchored root" on the audit page)."""
    epochs = group_epochs(entries, epoch_size=epoch_size)
    for epoch in epochs:
        if epoch["from_index"] <= position <= epoch["to_index"]:
            local = position - epoch["from_index"]
            hashes = [
                e.get("invoiceHash")
                for e in entries[epoch["from_index"] : epoch["to_index"] + 1]
                if e.get("invoiceHash")
            ]
            if local >= len(hashes):
                return None
            return {
                "epoch": epoch["index"],
                "root": epoch["root"],
                "leaf": hashes[local],
                "proof": merkle_proof(hashes, local),
            }
    return None
