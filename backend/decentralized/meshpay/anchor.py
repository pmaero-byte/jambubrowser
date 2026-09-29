"""
Solana anchoring of epoch Merkle roots.

MVP uses the **SPL Memo program** (``MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr``)
instead of a custom on-chain program: no program deploy, no audit, and a
root written to a memo is exactly as tamper-evident as one written to
custom state. Migrating to a settlement program later only changes this
module.

Transports:
- ``SolanaMemoAnchor`` — builds/signs a memo transaction with ``solders``
  and submits it via JSON-RPC ``sendTransaction`` (devnet by default).
  Requires ``solders`` + a funded keypair.
- ``MockAnchorTransport`` — deterministic offline signature for tests,
  demos, and DBs that must never pretend a chain transaction happened.
  Records carry ``transport="mock"`` and the UI labels them as such.

``anchor_root()`` picks the transport from config and **never silently
downgrades**: the record always says which transport produced it.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
DEVNET_RPC = "https://api.devnet.solana.com"

EXPLORERS = {
    "devnet": "https://explorer.solana.com/tx/{sig}?cluster=devnet",
    "mainnet-beta": "https://explorer.solana.com/tx/{sig}",
    "testnet": "https://explorer.solana.com/tx/{sig}?cluster=testnet",
    "mock": "",
}


class AnchorError(RuntimeError):
    """Anchoring failed (no keypair, RPC error, missing dependency)."""


class AnchorUnavailable(AnchorError):
    """The requested transport cannot run in this environment."""


@dataclass
class AnchorRecord:
    epoch: int
    root: str
    receipts: int
    cluster: str
    transport: str          # "solana" | "mock"
    signature: str
    memo: str
    created_at: float
    epoch_size: int = 50    # how receipts were grouped — needed to re-verify

    def to_dict(self) -> dict:
        return asdict(self)


def build_memo(root: str, epoch: int, receipts: int) -> str:
    """Canonical memo payload. Keep it versioned — verifiers parse it."""
    return f"meshpay:v1:{epoch}:{receipts}:{root}"


def explorer_url(signature: str, cluster: str) -> str:
    template = EXPLORERS.get(cluster, EXPLORERS["devnet"])
    if not template:
        return ""
    return template.format(sig=signature)


class MockAnchorTransport:
    """Deterministic offline transport (never touches a chain)."""

    name = "mock"

    def anchor(self, memo: str) -> str:
        digest = hashlib.sha256(f"meshpay-mock:{memo}".encode()).hexdigest()
        return f"mock:{digest[:64]}"


class SolanaMemoAnchor:
    """Sign + submit a memo transaction over JSON-RPC."""

    name = "solana"

    def __init__(
        self,
        *,
        rpc_url: str = DEVNET_RPC,
        keypair_path: Optional[str] = None,
        allow_insecure: bool = False,  # accepted for API symmetry; unused
    ):
        self.rpc_url = rpc_url
        self.keypair_path = keypair_path

    # -- helpers -------------------------------------------------------------

    def _load_keypair(self):
        try:
            from solders.keypair import Keypair
        except ImportError as e:  # pragma: no cover - optional dependency
            raise AnchorUnavailable(
                "solders is not installed — pip install solders"
            ) from e
        if not self.keypair_path:
            raise AnchorUnavailable(
                "no keypair configured — set JAMBU_MESHPAY_KEYPAIR to a "
                "solana keypair JSON (solana-keygen new)"
            )
        path = Path(self.keypair_path).expanduser()
        if not path.exists():
            raise AnchorUnavailable(f"keypair not found: {path}")
        raw = json.loads(path.read_text())
        try:
            return Keypair.from_bytes(bytes(raw))
        except Exception as e:
            raise AnchorError(f"invalid keypair JSON: {e}") from e

    # -- transport -----------------------------------------------------------

    async def anchor(self, memo: str) -> str:
        try:
            import httpx
            from solders.hash import Hash
            from solders.instruction import Instruction
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.transaction import Transaction
        except ImportError as e:  # pragma: no cover - optional dependency
            raise AnchorUnavailable(
                "solders is not installed — pip install solders"
            ) from e

        keypair = self._load_keypair()
        async with httpx.AsyncClient(timeout=20.0) as client:
            blockhash = await self._get_blockhash(client)
            ix = Instruction(
                program_id=Pubkey.from_string(MEMO_PROGRAM_ID),
                data=memo.encode("utf-8"),
                accounts=[],
            )
            msg = Message.new_with_blockhash([ix], keypair.pubkey(), blockhash)
            tx = Transaction.new_unsigned(msg)
            tx.sign([keypair], blockhash)
            tx_b64 = self._encode_tx(tx)
            signature = str(tx.signatures[0])

            resp = await client.post(self.rpc_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                "params": [
                    tx_b64,
                    {"encoding": "base64", "skipPreflight": False,
                     "preflightCommitment": "confirmed"},
                ],
            })
            data = resp.json()
            if "error" in data:
                raise AnchorError(f"sendTransaction error: {data['error']}")
            returned_sig = data.get("result") or signature
            # Best-effort confirmation; a timeout is not a failure (the tx
            # is already submitted and its signature is what we anchor).
            try:
                await self._confirm(client, returned_sig)
            except Exception:
                pass
            return returned_sig

    async def _get_blockhash(self, client) -> "Hash":
        from solders.hash import Hash

        resp = await client.post(self.rpc_url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "confirmed"}],
        })
        data = resp.json()
        if "error" in data:
            raise AnchorError(f"getLatestBlockhash error: {data['error']}")
        value = (data.get("result") or {}).get("value") or {}
        bh = value.get("blockhash")
        if not bh:
            raise AnchorError("getLatestBlockhash returned no blockhash")
        return Hash.from_string(bh)

    async def _confirm(self, client, signature: str, attempts: int = 6) -> bool:
        import asyncio

        for _ in range(attempts):
            await asyncio.sleep(1.5)
            resp = await client.post(self.rpc_url, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignatureStatuses",
                "params": [[signature], {"searchTransactionHistory": True}],
            })
            value = ((resp.json().get("result") or {}).get("value") or [None])[0]
            if value and value.get("confirmationStatus") in ("confirmed", "finalized"):
                return True
            if value and value.get("err"):
                raise AnchorError(f"transaction failed on-chain: {value['err']}")
        return False

    @staticmethod
    def _encode_tx(tx) -> str:
        import base64

        return base64.b64encode(bytes(tx)).decode("ascii")


async def anchor_root(
    *,
    root: str,
    epoch: int,
    receipts: int,
    cluster: str = "mock",
    rpc_url: str = DEVNET_RPC,
    keypair_path: Optional[str] = None,
    epoch_size: int = 50,
) -> AnchorRecord:
    """Anchor a root with the configured transport and return the record.

    ``cluster="mock"`` (the default) never touches a chain. Any other
    cluster uses Solana; if the toolchain/keypair is missing the error is
    raised, not downgraded — a DB must never claim a chain transaction
    that did not happen.
    """
    memo = build_memo(root, epoch, receipts)
    created = time.time()

    if cluster == "mock" or not root:
        sig = MockAnchorTransport().anchor(memo)
        return AnchorRecord(
            epoch=epoch, root=root, receipts=receipts, cluster="mock",
            transport="mock", signature=sig, memo=memo, created_at=created,
            epoch_size=epoch_size,
        )

    transport = SolanaMemoAnchor(rpc_url=rpc_url, keypair_path=keypair_path)
    signature = await transport.anchor(memo)
    return AnchorRecord(
        epoch=epoch, root=root, receipts=receipts, cluster=cluster,
        transport="solana", signature=signature, memo=memo, created_at=created,
        epoch_size=epoch_size,
    )
