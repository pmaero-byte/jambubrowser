"""MeshPay configuration (env-driven, single source for routes + CLI)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "") or default)
    except ValueError:
        return default


@dataclass
class MeshPayConfig:
    cluster: str = "mock"              # mock | devnet | testnet | mainnet-beta
    rpc_url: str = "https://api.devnet.solana.com"
    keypair: str = ""                  # path to solana keypair JSON
    dct_usd_rate: float = 0.01         # configured rate — NOT an oracle
    protocol_fee_pct: float = 0.15
    epoch_size: int = 50

    @classmethod
    def from_env(cls) -> "MeshPayConfig":
        return cls(
            cluster=(os.environ.get("JAMBU_MESHPAY_CLUSTER", "") or "mock").strip(),
            rpc_url=os.environ.get(
                "JAMBU_MESHPAY_RPC_URL", "https://api.devnet.solana.com",
            ).strip(),
            keypair=(os.environ.get("JAMBU_MESHPAY_KEYPAIR", "") or "").strip(),
            dct_usd_rate=_env_float("JAMBU_MESHPAY_DCT_USD", 0.01),
            protocol_fee_pct=_env_float("JAMBU_MESHPAY_FEE_PCT", 0.15),
            epoch_size=_env_int("JAMBU_MESHPAY_EPOCH_SIZE", 50),
        )

    def describe(self) -> dict:
        """Public, secret-free description for the UI."""
        return {
            "cluster": self.cluster,
            "rpc_url": self.rpc_url if self.cluster != "mock" else "",
            "has_keypair": bool(self.keypair),
            "dct_usd_rate": self.dct_usd_rate,
            "protocol_fee_pct": self.protocol_fee_pct,
            "epoch_size": self.epoch_size,
            "transport": (
                "mock (no chain transactions)" if self.cluster == "mock"
                else f"solana:{self.cluster}"
            ),
        }
