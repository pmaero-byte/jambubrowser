"""
MeshPay Stage 1 — wallets, payout batches, and prepared USDC transactions.

Stage 0 proved *what is owed* (anchored epoch roots). Stage 1 makes it
**payable** without pretending money moved when it didn't:

1. **Wallet binding** — DCM receipts identify providers by ``nodeId``; this
   module maps them to wallets (validated base58 Solana addresses) in
   ``meshpay_wallets``. Unbound providers are skipped and reported, never
   silently dropped.
2. **Payout batches** — an epoch's ``payout_plan`` becomes a persisted
   batch of instructions (amounts in atomic USDC units) with a status
   (``planned`` → ``approved`` → ``prepared``/``broadcast``/``failed``).
3. **Transaction preparation** — for each instruction we build a real SPL
   ``transfer_checked`` plus an idempotent associated-token-account creation
   (so a first payout to a fresh wallet works). With
   ``JAMBU_MESHPAY_CLUSTER=mock`` (the default) the transaction is *built
   and serialized for review* and the batch records
   ``status="prepared"`` — it is not broadcast, and nothing claims it was.
   With a real cluster and a funded treasury keypair the same builder signs
   and broadcasts via JSON-RPC.
4. **Reconciliation** — a batch re-checks against the live receipt window:
   every payable provider's amount must still match the plan, and the epoch
   root (when anchored) is compared against the anchor log.

Approval is **fail-closed**: broadcasting-equivalent actions require
``JAMBU_ADMIN_API_KEY`` to be configured and presented. There is no default
"dev" approver — an unset key means payouts cannot be approved at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from backend.modules.meshpay import MeshPayConfig
from backend.modules.meshpay.plan import payout_plan

log = logging.getLogger("jambu.meshpay.payouts")

USDC_DECIMALS = 6

# Well-known Solana program ids (mainnet + devnet share these).
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
RENT_SYSVAR_ID = "SysvarRent111111111111111111111111111111111"
# USDC mint. Mainnet default is the canonical mint; operators on devnet MUST
# verify and override JAMBU_MESHPAY_USDC_MINT (devnet mints change).
USDC_MINT_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def usdc_mint() -> str:
    return (os.environ.get("JAMBU_MESHPAY_USDC_MINT") or USDC_MINT_MAINNET).strip()


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------

def validate_solana_address(address: str) -> str:
    """Validate a base58 Solana address; returns it normalized."""
    if not address or not address.strip():
        raise ValueError("wallet_address must not be empty")
    try:
        from solders.pubkey import Pubkey

        return str(Pubkey.from_string(address.strip()))
    except ImportError as e:  # pragma: no cover - optional dependency
        raise ValueError("solders is not installed — pip install solders") from e
    except Exception as e:
        raise ValueError(f"invalid Solana address: {e}") from e


def bind_wallet(node_id: str, wallet_address: str, source: str = "manual") -> dict:
    wallet = validate_solana_address(wallet_address)
    if not node_id.strip():
        raise ValueError("node_id must not be empty")
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO meshpay_wallets (node_id, wallet_address, source, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                wallet_address = excluded.wallet_address,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (node_id.strip(), wallet, source, time.time()),
        )
        conn.commit()
    return {"node_id": node_id.strip(), "wallet_address": wallet, "source": source}


def list_wallets() -> list[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meshpay_wallets ORDER BY node_id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_wallets_map() -> dict[str, str]:
    return {w["node_id"]: w["wallet_address"] for w in list_wallets()}


def unbind_wallet(node_id: str) -> bool:
    from backend.core.database import get_db

    with get_db() as conn:
        cur = conn.execute("DELETE FROM meshpay_wallets WHERE node_id = ?", (node_id,))
        conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

def _to_atomic(usdc: float) -> int:
    return int(round(float(usdc) * 10 ** USDC_DECIMALS))


def build_payout_batch(
    entries: list[dict],
    *,
    epoch_index: int = -1,
    epoch_size: int = 50,
    dct_usd_rate: float = 0.01,
    protocol_fee_pct: float = 0.15,
    wallets: Optional[dict[str, str]] = None,
) -> dict:
    """Turn an epoch's payout plan into payable instructions."""
    from backend.modules.meshpay.plan import group_epochs

    wallets = wallets if wallets is not None else get_wallets_map()
    epochs = group_epochs(entries, epoch_size=epoch_size)
    if not epochs:
        raise ValueError("no receipts to plan a payout from")
    index = epoch_index if epoch_index >= 0 else len(epochs) - 1
    if index >= len(epochs):
        raise ValueError(f"epoch_index out of range (0..{len(epochs) - 1})")
    plan = payout_plan(
        entries, epoch_size=epoch_size, epoch_index=index,
        dct_usd_rate=dct_usd_rate, protocol_fee_pct=protocol_fee_pct,
    )
    instructions: list[dict] = []
    unbound: list[dict] = []
    for provider in plan["providers"]:
        node_id = provider["nodeId"]
        wallet = wallets.get(node_id)
        if not wallet:
            unbound.append({
                "nodeId": node_id, "usdc": provider["usdc"],
                "reason": "no wallet bound",
            })
            continue
        instructions.append({
            "node_id": node_id,
            "wallet": wallet,
            "amount_atomic": _to_atomic(provider["usdc"]),
            "usdc": provider["usdc"],
            "receipts": provider["receipts"],
            "net_dct": provider["netDct"],
        })

    return {
        "epoch_index": plan["epoch"]["index"],
        "epoch_size": epoch_size,
        "epoch_root": plan["epoch"]["root"],
        "dct_usd_rate": dct_usd_rate,
        "protocol_fee_pct": protocol_fee_pct,
        "instructions": instructions,
        "unbound": unbound,
        "totals": {
            "planned_usdc": plan["totals"]["usdc"],
            "payable_usdc": round(sum(i["usdc"] for i in instructions), 6),
            "payable_atomic": sum(i["amount_atomic"] for i in instructions),
            "unbound_usdc": round(sum(u["usdc"] for u in unbound), 6),
        },
        "rate_note": plan["rate_note"],
    }


def save_batch(batch: dict) -> dict:
    from backend.core.database import get_db

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO meshpay_payouts
                (epoch_index, epoch_size, epoch_root, dct_usd_rate,
                 protocol_fee_pct, status, cluster, transport,
                 total_usdc, payable_usdc, instructions_json, unbound_json,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch["epoch_index"], batch["epoch_size"], batch.get("epoch_root"),
                batch["dct_usd_rate"], batch["protocol_fee_pct"],
                MeshPayConfig.from_env().cluster,
                "prepared" if MeshPayConfig.from_env().is_mock else "solana",
                batch["totals"]["planned_usdc"],
                batch["totals"]["payable_usdc"],
                json.dumps(batch["instructions"]),
                json.dumps(batch["unbound"]),
                time.time(), time.time(),
            ),
        )
        conn.commit()
        batch_id = cur.lastrowid
    out = dict(batch)
    out["id"] = batch_id
    out["status"] = "planned"
    return out


def list_batches(limit: int = 50) -> list[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, epoch_index, status, cluster, transport, total_usdc,"
            " payable_usdc, tx, approved_by, created_at, executed_at, error"
            " FROM meshpay_payouts ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_batch(batch_id: int) -> Optional[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM meshpay_payouts WHERE id = ?", (batch_id,),
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    if "tx" in record:
        record["transaction"] = record.pop("tx")
    for field in ("instructions_json", "unbound_json"):
        try:
            record[field.replace("_json", "")] = json.loads(record.pop(field) or "[]")
        except ValueError:
            record[field.replace("_json", "")] = []
    return record


def _update_batch(batch_id: int, **fields) -> None:
    allowed = {
        "status", "transaction", "approved_by", "approved_at", "executed_at",
        "error", "instructions_json",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if "transaction" in updates:  # `transaction` is reserved in SQLite
        updates["tx"] = updates.pop("transaction")
    if not updates:
        return
    updates["updated_at"] = time.time()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute(
            f"UPDATE meshpay_payouts SET {assignments} WHERE id = ?",
            list(updates.values()) + [batch_id],
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Approval (fail closed)
# ---------------------------------------------------------------------------

def approve_batch(batch_id: int, admin_key: Optional[str], approved_by: str = "operator") -> dict:
    configured = (os.environ.get("JAMBU_ADMIN_API_KEY") or "").strip()
    if not configured:
        raise PermissionError(
            "payout approval is disabled: set JAMBU_ADMIN_API_KEY and send it "
            "in the X-Admin-Api-Key header"
        )
    if not admin_key or admin_key != configured:
        raise PermissionError("invalid admin key")

    batch = get_batch(batch_id)
    if batch is None:
        raise LookupError(f"payout batch not found: {batch_id}")
    if batch["status"] != "planned":
        raise ValueError(f"batch is {batch['status']}, not planned")
    if not batch.get("instructions"):
        raise ValueError("batch has no payable instructions (bind provider wallets first)")

    _update_batch(
        batch_id, status="approved", approved_by=approved_by, approved_at=time.time(),
    )
    return get_batch(batch_id)


# ---------------------------------------------------------------------------
# Transaction construction
# ---------------------------------------------------------------------------

def _derive_ata(owner: str, mint: str) -> str:
    from solders.pubkey import Pubkey

    ata, _bump = Pubkey.find_program_address(
        [
            bytes(Pubkey.from_string(owner)),
            bytes(Pubkey.from_string(TOKEN_PROGRAM_ID)),
            bytes(Pubkey.from_string(mint)),
        ],
        Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID),
    )
    return str(ata)


def build_payout_transaction(batch: dict, *, treasury: str, mint: Optional[str] = None):
    """Build (not sign, not send) the payout transaction for a batch.

    Per instruction: an idempotent ATA creation for the provider, then an SPL
    ``transfer_checked`` from the treasury's ATA. Returns the solders
    ``Message`` pieces needed for signing; callers decide about keys.
    """
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    mint = mint or usdc_mint()
    treasury_pubkey = Pubkey.from_string(treasury)
    treasury_ata = _derive_ata(treasury, mint)

    instructions = []
    for item in batch.get("instructions") or []:
        provider_ata = _derive_ata(item["wallet"], mint)
        # CreateIdempotent: no-op if the provider's ATA already exists.
        instructions.append(Instruction(
            program_id=Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID),
            accounts=[
                AccountMeta(pubkey=treasury_pubkey, is_signer=True, is_writable=True),
                AccountMeta(pubkey=Pubkey.from_string(provider_ata), is_signer=False, is_writable=True),
                AccountMeta(pubkey=Pubkey.from_string(item["wallet"]), is_signer=False, is_writable=False),
                AccountMeta(pubkey=Pubkey.from_string(mint), is_signer=False, is_writable=False),
                AccountMeta(pubkey=Pubkey.from_string(SYSTEM_PROGRAM_ID), is_signer=False, is_writable=False),
                AccountMeta(pubkey=Pubkey.from_string(TOKEN_PROGRAM_ID), is_signer=False, is_writable=False),
                AccountMeta(pubkey=Pubkey.from_string(RENT_SYSVAR_ID), is_signer=False, is_writable=False),
            ],
            data=bytes([1]),  # CreateIdempotent
        ))
        # transfer_checked: [12, amount u64 LE, decimals u8]
        amount = int(item["amount_atomic"])
        data = bytes([12]) + amount.to_bytes(8, "little") + bytes([USDC_DECIMALS])
        instructions.append(Instruction(
            program_id=Pubkey.from_string(TOKEN_PROGRAM_ID),
            accounts=[
                AccountMeta(pubkey=Pubkey.from_string(treasury_ata), is_signer=False, is_writable=True),
                AccountMeta(pubkey=Pubkey.from_string(mint), is_signer=False, is_writable=False),
                AccountMeta(pubkey=Pubkey.from_string(provider_ata), is_signer=False, is_writable=True),
                AccountMeta(pubkey=treasury_pubkey, is_signer=True, is_writable=False),
            ],
            data=data,
        ))
    return {
        "treasury": str(treasury_pubkey),
        "treasury_ata": treasury_ata,
        "mint": mint,
        "instructions": instructions,
    }


def execute_batch(batch_id: int, *, keypair_path: Optional[str] = None) -> dict:
    """Prepare (mock/dev) or broadcast (real cluster + funded treasury).

    Mock clusters return ``status="prepared"`` with a base64 transaction for
    review — never a fake signature. Real clusters require a treasury
    keypair; failures are recorded, not swallowed.
    """
    batch = get_batch(batch_id)
    if batch is None:
        raise LookupError(f"payout batch not found: {batch_id}")
    if batch["status"] != "approved":
        raise ValueError(f"batch is {batch['status']}, not approved")

    cfg = MeshPayConfig.from_env()
    keypair_path = keypair_path or cfg.keypair or None

    if len(batch.get("instructions") or []) == 0:
        raise ValueError("batch has no payable instructions")

    if cfg.is_mock or not keypair_path:
        # Build a *reviewable* transaction: for real clusters we still build
        # (so the operator sees exactly what would be sent) but never claim
        # it was broadcast without a keypair.
        built = None
        serialized = None
        treasury = (os.environ.get("JAMBU_MESHPAY_TREASURY") or "").strip()
        treasury_placeholder = not treasury
        try:
            from solders.hash import Hash
            from solders.keypair import Keypair
            from solders.message import Message
            from solders.transaction import Transaction

            if not treasury:
                treasury = str(Keypair().pubkey())  # shape preview only
            built = build_payout_transaction(batch, treasury=treasury)
            blockhash = Hash.new_unique()
            msg = Message.new_with_blockhash(built["instructions"], None, blockhash)
            tx = Transaction.new_unsigned(msg)
            serialized = __import__("base64").b64encode(bytes(tx)).decode("ascii")
        except ImportError:  # pragma: no cover - optional dependency
            pass

        digest = hashlib.sha256(
            json.dumps(batch["instructions"], sort_keys=True).encode()
        ).hexdigest()
        _update_batch(
            batch_id, status="prepared",
            transaction=f"prepared:{digest[:64]}",
            executed_at=time.time(),
            error=(
                "mock cluster: transaction prepared for review, not broadcast"
                if cfg.is_mock else
                "no treasury keypair — transaction prepared for review, not broadcast"
            ),
        )
        result = get_batch(batch_id)
        result["serialized_transaction"] = serialized
        result["instruction_count"] = len(built["instructions"]) if built else None
        result["treasury"] = treasury or None
        result["treasury_is_placeholder"] = treasury_placeholder
        result["usdc_mint"] = usdc_mint()
        return result

    # Real cluster with a treasury keypair: sign + broadcast.
    try:
        import base64

        import httpx

        from solders.keypair import Keypair
        from solders.message import Message
        from solders.transaction import Transaction

        raw = json.loads(open(os.path.expanduser(keypair_path)).read())
        treasury_key = Keypair.from_bytes(bytes(raw))
        built = build_payout_transaction(batch, treasury=str(treasury_key.pubkey()))

        with httpx.Client(timeout=20.0) as client:
            response = client.post(cfg.rpc_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash",
                "params": [{"commitment": "confirmed"}],
            })
            value = (response.json().get("result") or {}).get("value") or {}
            from solders.hash import Hash

            blockhash = Hash.from_string(value["blockhash"])
            message = Message.new_with_blockhash(
                built["instructions"], treasury_key.pubkey(), blockhash,
            )
            tx = Transaction.new_unsigned(message)
            tx.sign([treasury_key], blockhash)
            encoded = base64.b64encode(bytes(tx)).decode("ascii")
            send = client.post(cfg.rpc_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                "params": [encoded, {"encoding": "base64", "skipPreflight": False}],
            })
            data = send.json()
            if "error" in data:
                raise RuntimeError(f"sendTransaction error: {data['error']}")
            signature = data.get("result") or str(tx.signatures[0])
        _update_batch(
            batch_id, status="broadcast", transaction=signature,
            executed_at=time.time(), error=None,
        )
        return get_batch(batch_id)
    except Exception as e:
        _update_batch(batch_id, status="failed", error=str(e), executed_at=time.time())
        raise


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile_batch(batch_id: int, entries: list[dict]) -> dict:
    """Re-check a batch against the current receipt window."""
    batch = get_batch(batch_id)
    if batch is None:
        raise LookupError(f"payout batch not found: {batch_id}")

    # Zero-valued economics are legitimate (0% fee) — never coalesce with `or`.
    rate = batch.get("dct_usd_rate")
    rate = 0.01 if rate is None else rate
    fee_pct = batch.get("protocol_fee_pct")
    fee_pct = 0.15 if fee_pct is None else fee_pct

    plan = payout_plan(
        entries, epoch_size=batch["epoch_size"], epoch_index=batch["epoch_index"],
        dct_usd_rate=rate, protocol_fee_pct=fee_pct,
    )
    expected = {p["nodeId"]: _to_atomic(p["usdc"]) for p in plan["providers"]}
    mismatches = []
    for item in batch.get("instructions") or []:
        want = expected.get(item["node_id"])
        if want is None:
            mismatches.append({
                "node_id": item["node_id"],
                "reason": "provider no longer present in the epoch",
            })
        elif want != item["amount_atomic"]:
            mismatches.append({
                "node_id": item["node_id"],
                "expected_atomic": want,
                "recorded_atomic": item["amount_atomic"],
                "reason": "amount drifted from the current receipt window",
            })

    anchored = None
    try:
        from backend.core.database import get_db

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM meshpay_anchors WHERE epoch = ? ORDER BY created_at DESC LIMIT 1",
                (batch["epoch_index"],),
            ).fetchone()
        if row:
            anchored = {
                "signature": row["signature"], "cluster": row["cluster"],
                "transport": row["transport"], "root": row["root"],
            }
    except Exception:
        anchored = None

    return {
        "batch_id": batch_id,
        "consistent": not mismatches,
        "mismatches": mismatches,
        "epoch_index": batch["epoch_index"],
        "anchor": anchored,
        "root_matches_anchor": (
            anchored is not None and anchored["root"] == batch.get("epoch_root")
        ) if anchored else None,
    }
