"""
MeshPay tests — JS-faithful serialization, independent chain verification,
Merkle proofs, payout planning, anchoring, and the /meshpay/* routes.

The serializer table in ``TestJsNumber`` is generated from **real Node.js**
(``node -e "JSON.stringify(...)"``) so the "independent" verifier is only
trusted after it reproduces the reference implementation's bytes.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.modules.meshpay import (
    MeshPayConfig,
    anchor_root,
    js_dumps,
    js_number,
    merkle_root,
    payout_plan,
    receipt_proof,
    verify_chain,
)
from backend.modules.meshpay.merkle import merkle_proof, verify_proof
from backend.modules.meshpay.anchor import (
    AnchorUnavailable,
    MockAnchorTransport,
    build_memo,
    explorer_url,
)


def run(coro):
    return asyncio.run(coro)


def _hash_payload(payload: dict) -> str:
    from backend.modules.meshpay import hash_receipt
    return hash_receipt(payload)


def make_entry(kind: str, prev: str | None, **fields) -> dict:
    """Build a DCM-shaped receipt (same insertion order as billingEngine)."""
    entry = {"kind": kind, "timestamp": 1700000000000, "prevInvoiceHash": prev}
    entry.update(fields)
    entry["invoiceHash"] = _hash_payload(entry)
    return entry


# ---------------------------------------------------------------------------
# JS-faithful serialization
# ---------------------------------------------------------------------------

# (value, expected JSON.stringify output) — generated with Node.js v24.
JS_NUMBER_TABLE = [
    (0, "0"), (-0.0, "0"), (1, "1"), (-1, "-1"), (42.0, "42"),
    (0.1, "0.1"), (0.00001, "0.00001"), (0.00005, "0.00005"),
    (0.00015, "0.00015"), (0.000001, "0.000001"), (0.0000001, "1e-7"),
    (0.00000015, "1.5e-7"), (1e20, "100000000000000000000"),
    (1e21, "1e+21"), (1.2345e21, "1.2345e+21"),
    (123456789.123, "123456789.123"), (0.30000000000000004, "0.30000000000000004"),
    (33.333333333333336, "33.333333333333336"),
    (-0.000000025, "-2.5e-8"), (3.141592653589793, "3.141592653589793"),
    (0.000123456, "0.000123456"), (12345.6789, "12345.6789"),
    (5e-324, "5e-324"), (1.7976931348623157e308, "1.7976931348623157e+308"),
]


class TestJsNumber:
    @pytest.mark.parametrize("value,expected", JS_NUMBER_TABLE)
    def test_matches_node_output(self, value, expected):
        assert js_number(value) == expected

    def test_non_finite_becomes_null(self):
        assert js_number(float("nan")) == "null"
        assert js_number(float("inf")) == "null"

    def test_big_ints_pass_through(self):
        assert js_number(2**53) == "9007199254740992"


class TestJsDumps:
    def test_object_key_order_is_preserved(self):
        payload = {"b": 1, "a": 2, "nested": {"z": True, "y": None}}
        assert js_dumps(payload) == '{"b":1,"a":2,"nested":{"z":true,"y":null}}'

    def test_strings_are_not_ascii_escaped(self):
        assert js_dumps({"s": "café ☕"}) == '{"s":"café ☕"}'
        assert js_dumps({"s": 'quote " and \\'}) == '{"s":"quote \\" and \\\\"}'

    def test_lists_and_bools(self):
        assert js_dumps([1, True, False, None]) == "[1,true,false,null]"

    def test_dcm_rate_pair_roundtrip(self):
        # The exact values DCM puts in inference-charge receipts.
        entry = {"grossChargeDct": 0.00001, "chargedDct": 0.00005}
        assert js_dumps(entry) == '{"grossChargeDct":0.00001,"chargedDct":0.00005}'


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------

class TestVerifyChain:
    def _chain(self, n: int = 3) -> list[dict]:
        entries: list[dict] = []
        prev = None
        for i in range(n):
            entry = make_entry(
                "usage", prev, nodeId=f"peer-{i}", cpu=100, bandwidth=200,
                reward=0.00001 * (i + 1), payoutMultiplier=1.0,
                executionHash=f"exec{i}", appHash="app", func="f", i32Args=None,
                stats=None, meteringSource="execMs-proxy",
            )
            entries.append(entry)
            prev = entry["invoiceHash"]
        return entries

    def test_valid_chain(self):
        entries = self._chain(4)
        verdict = verify_chain(entries)
        assert verdict["valid"] is True
        assert verdict["checked"] == 4
        assert verdict["head_hash"] == entries[-1]["invoiceHash"]
        assert verdict["window_truncated"] is False
        assert verdict["kinds"] == {"usage": 4}

    def test_tampered_field_is_detected(self):
        entries = self._chain(3)
        entries[1]["reward"] = 999.0  # attacker changes the payout
        verdict = verify_chain(entries)
        assert verdict["valid"] is False
        assert verdict["broken_at"] == 1
        assert "hash mismatch" in verdict["broken_reason"]

    def test_tampered_link_is_detected(self):
        entries = self._chain(3)
        entries[2]["prevInvoiceHash"] = "0" * 64
        entries[2]["invoiceHash"] = _hash_payload(
            {k: v for k, v in entries[2].items() if k != "invoiceHash"}
        )
        verdict = verify_chain(entries)
        assert verdict["valid"] is False
        assert verdict["broken_at"] == 2
        assert "chain link broken" in verdict["broken_reason"]

    def test_windowed_log_is_flagged_truncated(self):
        full = self._chain(6)
        window = full[2:]  # e.g. DCM returns only the last N entries
        verdict = verify_chain(window)
        assert verdict["valid"] is True  # links inside the window are sound
        assert verdict["window_truncated"] is True
        assert verdict["first_prev_hash"] == full[1]["invoiceHash"]

    def test_charges_and_settlements_aggregate_like_dcm(self):
        e1 = make_entry(
            "inference-charge", None, did="did:dcm:a", inputTokens=10,
            outputTokens=20, grossChargeDct=0.0011, subsidyApplied=False,
            subsidyDiscount=0, chargedDct=0.0011, balanceAfter=5.0,
        )
        e2 = make_entry(
            "usage", e1["invoiceHash"], nodeId="peer-1", cpu=1, bandwidth=1,
            reward=0.02, payoutMultiplier=1.0, executionHash=None, appHash=None,
            func=None, i32Args=None, stats=None, meteringSource="execMs-proxy",
        )
        e3 = make_entry(
            "settlement", e2["invoiceHash"], nodeId="peer-1", netReward=0.017,
            protocolCommission=0.003,
            treasuryDistribution={"burned": 0.001, "grants": 0.001, "insurance": 0.001},
        )
        verdict = verify_chain([e1, e2, e3])
        assert verdict["valid"] is True
        assert verdict["totals"]["totalCharged"] == pytest.approx(0.0011)
        assert verdict["totals"]["totalMinted"] == pytest.approx(0.017)
        assert verdict["totals"]["totalBurned"] == pytest.approx(0.001)
        assert verdict["account_deltas"]["did:dcm:a"] == pytest.approx(-0.0011)
        assert verdict["account_deltas"]["peer-1"] == pytest.approx(0.017)

    def test_anonymous_charge_has_no_balance_delta(self):
        e = make_entry(
            "inference-charge", None, did="anonymous", inputTokens=1,
            outputTokens=1, grossChargeDct=0.00006, subsidyApplied=False,
            subsidyDiscount=0, chargedDct=0.00006, balanceAfter=None,
        )
        verdict = verify_chain([e])
        assert verdict["valid"] is True
        assert verdict["totals"]["totalCharged"] == pytest.approx(0.00006)
        assert "anonymous" not in verdict["account_deltas"]

    def test_empty_chain_is_valid(self):
        verdict = verify_chain([])
        assert verdict["valid"] is True
        assert verdict["checked"] == 0
        assert verdict["head_hash"] is None


# ---------------------------------------------------------------------------
# Merkle
# ---------------------------------------------------------------------------

class TestMerkle:
    def _leaves(self, n: int) -> list[str]:
        return [f"{i:064x}" for i in range(1, n + 1)]

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8])
    def test_root_is_deterministic_and_proofs_verify(self, n):
        leaves = self._leaves(n)
        root = merkle_root(leaves)
        assert root == merkle_root(leaves)
        for i in range(n):
            proof = merkle_proof(leaves, i)
            assert verify_proof(leaves[i], proof, root) is True, f"leaf {i}"

    def test_empty_returns_none(self):
        assert merkle_root([]) is None

    def test_proof_rejects_wrong_leaf(self):
        leaves = self._leaves(4)
        root = merkle_root(leaves)
        proof = merkle_proof(leaves, 0)
        assert verify_proof(self._leaves(4)[3], proof, root) is False


# ---------------------------------------------------------------------------
# Payout planning
# ---------------------------------------------------------------------------

class TestPayoutPlan:
    def _usage_entries(self) -> list[dict]:
        entries = []
        prev = None
        for i, (node, reward) in enumerate([
            ("peer-a", 0.05), ("peer-b", 0.03), ("peer-a", 0.02),
        ]):
            entry = make_entry(
                "usage", prev, nodeId=node, cpu=1, bandwidth=1, reward=reward,
                payoutMultiplier=1.0, executionHash=None, appHash=None,
                func=None, i32Args=None, stats=None, meteringSource="execMs-proxy",
            )
            entries.append(entry)
            prev = entry["invoiceHash"]
        return entries

    def test_groups_by_provider_and_applies_fee(self):
        plan = payout_plan(
            self._usage_entries(), epoch_size=50, dct_usd_rate=0.01,
            protocol_fee_pct=0.15,
        )
        providers = {p["nodeId"]: p for p in plan["providers"]}
        assert providers["peer-a"]["grossDct"] == pytest.approx(0.07)
        assert providers["peer-a"]["netDct"] == pytest.approx(0.0595)
        assert providers["peer-a"]["usdc"] == pytest.approx(0.000595)
        assert providers["peer-b"]["receipts"] == 1
        assert plan["totals"]["grossDct"] == pytest.approx(0.10)
        assert plan["totals"]["feeDct"] == pytest.approx(0.015)
        assert "not an oracle" in plan["rate_note"]

    def test_epoch_split(self):
        plan = payout_plan(
            self._usage_entries(), epoch_size=2, epoch_index=1,
            dct_usd_rate=0.01, protocol_fee_pct=0.0,
        )
        assert plan["epoch"]["from_index"] == 2
        assert plan["epoch"]["to_index"] == 2
        assert plan["totals"]["grossDct"] == pytest.approx(0.02)

    def test_epoch_index_out_of_range(self):
        with pytest.raises(ValueError):
            payout_plan(self._usage_entries(), epoch_size=2, epoch_index=5)

    def test_receipt_proof_locates_leaf(self):
        entries = self._usage_entries()
        proof = receipt_proof(entries, 1, epoch_size=50)
        assert proof["leaf"] == entries[1]["invoiceHash"]
        assert verify_proof(proof["leaf"], proof["proof"], proof["root"]) is True


# ---------------------------------------------------------------------------
# Anchoring
# ---------------------------------------------------------------------------

class TestAnchoring:
    def test_mock_anchor_is_deterministic_and_labeled(self):
        record = run(anchor_root(root="ab" * 32, epoch=3, receipts=7))
        assert record.transport == "mock"
        assert record.cluster == "mock"
        assert record.signature.startswith("mock:")
        assert record.root == "ab" * 32
        same = run(anchor_root(root="ab" * 32, epoch=3, receipts=7))
        assert same.signature == record.signature
        different = run(anchor_root(root="cd" * 32, epoch=3, receipts=7))
        assert different.signature != record.signature

    def test_memo_format_is_versioned(self):
        assert build_memo("ab" * 32, 5, 50).startswith("meshpay:v1:5:50:")
        parsed = build_memo("ab" * 32, 5, 50).split(":")
        assert parsed[0] == "meshpay" and parsed[1] == "v1"

    def test_devnet_without_keypair_raises_not_downgrades(self):
        with pytest.raises(AnchorUnavailable):
            run(anchor_root(
                root="ab" * 32, epoch=0, receipts=1, cluster="devnet",
                keypair_path=None,
            ))

    def test_missing_keypair_file_raises(self):
        with pytest.raises(AnchorUnavailable):
            run(anchor_root(
                root="ab" * 32, epoch=0, receipts=1, cluster="devnet",
                keypair_path="/nonexistent/keypair.json",
            ))

    def test_explorer_urls(self):
        assert "cluster=devnet" in explorer_url("sig", "devnet")
        assert explorer_url("sig", "mainnet-beta") == "https://explorer.solana.com/tx/sig"
        assert explorer_url("sig", "mock") == ""

    def test_solana_transaction_builds_and_signs_offline(self):
        """Construct a real memo transaction without network access."""
        try:
            from solders.hash import Hash
            from solders.instruction import Instruction
            from solders.keypair import Keypair
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.transaction import Transaction
        except ImportError:  # pragma: no cover - optional dep
            pytest.skip("solders not installed")

        from backend.modules.meshpay.anchor import MEMO_PROGRAM_ID

        keypair = Keypair()
        memo = build_memo("ab" * 32, 0, 1)
        ix = Instruction(
            program_id=Pubkey.from_string(MEMO_PROGRAM_ID),
            data=memo.encode("utf-8"),
            accounts=[],
        )
        blockhash = Hash.new_unique()
        msg = Message.new_with_blockhash([ix], keypair.pubkey(), blockhash)
        tx = Transaction.new_unsigned(msg)
        tx.sign([keypair], blockhash)
        raw = bytes(tx)
        assert len(raw) > 100
        # Memo data must appear verbatim in the serialized instruction.
        assert memo.encode() in raw
        assert str(tx.signatures[0])


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_are_safe(self):
        cfg = MeshPayConfig.from_env()
        assert cfg.cluster == "mock"  # never anchors to a chain by default
        assert cfg.protocol_fee_pct == 0.15

    def test_describe_hides_rpc_for_mock(self):
        described = MeshPayConfig(cluster="mock").describe()
        assert described["rpc_url"] == ""
        assert "mock" in described["transport"]

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("JAMBAPAY_UNUSED", "x")  # no-op guard
        for key, value in (
            ("JAMBU_MESHPAY_CLUSTER", "devnet"),
            ("JAMBU_MESHPAY_DCT_USD", "0.42"),
            ("JAMBU_MESHPAY_FEE_PCT", "0.05"),
            ("JAMBU_MESHPAY_EPOCH_SIZE", "7"),
        ):
            monkeypatch.setenv(key, value)
        cfg = MeshPayConfig.from_env()
        assert cfg.cluster == "devnet"
        assert cfg.dct_usd_rate == pytest.approx(0.42)
        assert cfg.protocol_fee_pct == pytest.approx(0.05)
        assert cfg.epoch_size == 7
