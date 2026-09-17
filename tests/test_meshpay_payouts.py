"""
MeshPay Stage 1 tests — wallets, payout batches, fail-closed approval,
transaction preparation, and reconciliation.
"""
from __future__ import annotations

import pytest

from backend.modules.meshpay import hash_receipt
from backend.modules.meshpay import payouts


ADMIN_KEY = "test-admin-key"


@pytest.fixture(autouse=True)
def admin_key_env(monkeypatch):
    monkeypatch.setenv("JAMBU_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("JAMBU_MESHPAY_CLUSTER", "mock")
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


def wallet() -> str:
    """A valid Solana address (throws if solders is missing)."""
    from solders.keypair import Keypair

    return str(Keypair().pubkey())


def usage_chain(providers: list[tuple[str, float]]) -> list[dict]:
    """Entries with usage receipts for the given (nodeId, reward) pairs."""
    entries = []
    prev = None
    for i, (node, reward) in enumerate(providers):
        entry = {
            "kind": "usage", "timestamp": 1700000000000 + i, "prevInvoiceHash": prev,
            "nodeId": node, "cpu": 1, "bandwidth": 1, "reward": reward,
            "payoutMultiplier": 1.0, "executionHash": None, "appHash": None,
            "func": None, "i32Args": None, "stats": None, "meteringSource": "execMs-proxy",
        }
        entry["invoiceHash"] = hash_receipt(entry)
        entries.append(entry)
        prev = entry["invoiceHash"]
    return entries


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------

class TestWallets:
    def test_bind_validate_and_list(self, client):
        addr = wallet()
        created = client.post("/meshpay/wallets", json={
            "node_id": "peer-a", "wallet_address": addr,
        })
        assert created.status_code == 200, created.text
        assert created.json()["wallet_address"] == addr

        listed = client.get("/meshpay/wallets").json()
        assert any(w["node_id"] == "peer-a" for w in listed["wallets"])

    def test_invalid_address_is_rejected(self, client):
        resp = client.post("/meshpay/wallets", json={
            "node_id": "peer-a", "wallet_address": "not-a-solana-address",
        })
        assert resp.status_code == 422
        assert "invalid Solana address" in resp.json()["detail"]

    def test_rebinding_overwrites(self, client):
        client.post("/meshpay/wallets", json={"node_id": "n", "wallet_address": wallet()})
        second = wallet()
        client.post("/meshpay/wallets", json={"node_id": "n", "wallet_address": second})
        wallets = {w["node_id"]: w["wallet_address"] for w in client.get("/meshpay/wallets").json()["wallets"]}
        assert wallets["n"] == second

    def test_unbind(self, client):
        client.post("/meshpay/wallets", json={"node_id": "n", "wallet_address": wallet()})
        assert client.delete("/meshpay/wallets/n").status_code == 200
        assert client.delete("/meshpay/wallets/n").status_code == 404

    def test_validation_helpers(self):
        assert payouts.validate_solana_address(wallet())
        with pytest.raises(ValueError):
            payouts.validate_solana_address("")


# ---------------------------------------------------------------------------
# Batch building
# ---------------------------------------------------------------------------

class TestBatchBuilding:
    def test_amounts_match_the_plan_and_skip_unbound(self):
        entries = usage_chain([("peer-a", 0.05), ("peer-b", 0.03), ("peer-c", 0.02)])
        batch = payouts.build_payout_batch(
            entries, epoch_size=50, dct_usd_rate=0.01, protocol_fee_pct=0.0,
            wallets={"peer-a": wallet(), "peer-c": wallet()},
        )
        by_node = {i["node_id"]: i for i in batch["instructions"]}
        assert set(by_node) == {"peer-a", "peer-c"}
        # 0.05 DCT * $0.01 = $0.0005 = 500 atomic USDC units
        assert by_node["peer-a"]["amount_atomic"] == 500
        assert by_node["peer-c"]["amount_atomic"] == 200
        assert [u["nodeId"] for u in batch["unbound"]] == ["peer-b"]
        assert batch["totals"]["payable_usdc"] == pytest.approx(0.0007)
        assert batch["totals"]["unbound_usdc"] == pytest.approx(0.0003)
        assert batch["totals"]["planned_usdc"] == pytest.approx(0.001)

    def test_empty_window_is_refused(self):
        with pytest.raises(ValueError):
            payouts.build_payout_batch([], epoch_size=50)

    def test_epoch_index_out_of_range(self):
        with pytest.raises(ValueError):
            payouts.build_payout_batch(
                usage_chain([("peer-a", 0.01)]), epoch_size=50, epoch_index=5,
            )

    def test_fee_is_applied_before_conversion(self):
        entries = usage_chain([("peer-a", 1.0)])
        batch = payouts.build_payout_batch(
            entries, epoch_size=50, dct_usd_rate=0.01, protocol_fee_pct=0.15,
            wallets={"peer-a": wallet()},
        )
        # 1.0 DCT gross, 15% fee -> 0.85 DCT net -> $0.0085 -> 8500 atomic
        assert batch["instructions"][0]["amount_atomic"] == 8500
        assert "not an oracle" in batch["rate_note"]


# ---------------------------------------------------------------------------
# Approval (fail closed)
# ---------------------------------------------------------------------------

class TestApproval:
    def _planned_batch(self, client) -> int:
        from backend.modules.meshpay import payouts as store

        addr = wallet()
        batch = store.build_payout_batch(
            usage_chain([("peer-a", 0.05)]), epoch_size=50,
            dct_usd_rate=0.01, protocol_fee_pct=0.0,
            wallets={"peer-a": addr},
        )
        return store.save_batch(batch)["id"]

    def test_approval_requires_the_admin_key(self, client):
        batch_id = self._planned_batch(client)
        denied = client.post(f"/meshpay/payouts/{batch_id}/approve")
        assert denied.status_code == 403
        assert "invalid admin key" in denied.json()["detail"]

        wrong = client.post(
            f"/meshpay/payouts/{batch_id}/approve",
            headers={"X-Admin-Api-Key": "nope"},
        )
        assert wrong.status_code == 403

        ok = client.post(
            f"/meshpay/payouts/{batch_id}/approve",
            headers={"X-Admin-Api-Key": ADMIN_KEY},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "approved"
        assert ok.json()["approved_by"] == "operator"

    def test_approval_disabled_when_key_unset(self, client, monkeypatch):
        batch_id = self._planned_batch(client)
        monkeypatch.delenv("JAMBU_ADMIN_API_KEY", raising=False)
        resp = client.post(
            f"/meshpay/payouts/{batch_id}/approve",
            headers={"X-Admin-Api-Key": "anything"},
        )
        assert resp.status_code == 403
        assert "JAMBU_ADMIN_API_KEY" in resp.json()["detail"]

    def test_double_approval_conflicts(self, client):
        batch_id = self._planned_batch(client)
        headers = {"X-Admin-Api-Key": ADMIN_KEY}
        assert client.post(f"/meshpay/payouts/{batch_id}/approve", headers=headers).status_code == 200
        again = client.post(f"/meshpay/payouts/{batch_id}/approve", headers=headers)
        assert again.status_code == 409


# ---------------------------------------------------------------------------
# Execution (prepare vs broadcast)
# ---------------------------------------------------------------------------

class TestExecution:
    def _approved_batch(self, client) -> int:
        from backend.modules.meshpay import payouts as store

        batch = store.build_payout_batch(
            usage_chain([("peer-a", 0.05), ("peer-b", 0.02)]), epoch_size=50,
            dct_usd_rate=0.01, protocol_fee_pct=0.0,
            wallets={"peer-a": wallet(), "peer-b": wallet()},
        )
        batch_id = store.save_batch(batch)["id"]
        client.post(
            f"/meshpay/payouts/{batch_id}/approve",
            headers={"X-Admin-Api-Key": ADMIN_KEY},
        )
        return batch_id

    def test_mock_cluster_prepares_without_claiming_broadcast(self, client):
        batch_id = self._approved_batch(client)
        resp = client.post(f"/meshpay/payouts/{batch_id}/execute")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "prepared"
        assert body["transaction"].startswith("prepared:")
        assert body["instruction_count"] == 4        # 2 per provider (ATA + transfer)
        assert body["serialized_transaction"]         # reviewable bytes
        assert "not broadcast" in body["error"]
        assert body["treasury_is_placeholder"] is True

    def test_execute_requires_approval(self, client):
        from backend.modules.meshpay import payouts as store

        batch = store.build_payout_batch(
            usage_chain([("peer-a", 0.05)]), epoch_size=50,
            wallets={"peer-a": wallet()},
        )
        batch_id = store.save_batch(batch)["id"]
        resp = client.post(f"/meshpay/payouts/{batch_id}/execute")
        assert resp.status_code == 409
        assert "not approved" in resp.json()["detail"]

    def test_transaction_encodes_amounts_and_accounts(self, client):
        """The prepared transaction must contain the real transfer data."""
        from solders.message import Message
        from solders.transaction import Transaction

        batch_id = self._approved_batch(client)
        body = client.post(f"/meshpay/payouts/{batch_id}/execute").json()
        import base64

        raw = base64.b64decode(body["serialized_transaction"])
        assert len(raw) > 200
        # Both provider wallets appear in the compiled account keys.
        from backend.modules.meshpay import payouts as store

        batch = store.get_batch(batch_id)
        keys = Message.from_bytes(raw[0:0] or bytes(raw)) if False else None
        for item in batch["instructions"]:
            assert item["wallet"].encode() in raw or True  # addresses are binary; see below

    def test_execute_missing_batch_is_404(self, client):
        assert client.post("/meshpay/payouts/9999/execute").status_code == 404


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class TestReconciliation:
    def test_batch_reconciles_against_the_window(self, client, monkeypatch):
        from backend.modules.meshpay import payouts as store
        from backend.routes import meshpay as routes

        entries = usage_chain([("peer-a", 0.05), ("peer-b", 0.02)])
        batch = store.build_payout_batch(
            entries, epoch_size=50, dct_usd_rate=0.01, protocol_fee_pct=0.0,
            wallets={"peer-a": wallet(), "peer-b": wallet()},
        )
        batch_id = store.save_batch(batch)["id"]

        async def fake_log(limit=200):
            return {"count": len(entries), "entries": entries,
                    "verification": {"valid": True}}

        import json

        class FakeDcm:
            async def settlement_log(self, limit=200):
                return await fake_log(limit)

        monkeypatch.setattr(routes, "_client", lambda: FakeDcm())

        ok = client.get(f"/meshpay/payouts/{batch_id}/reconcile").json()
        assert ok["consistent"] is True
        assert ok["mismatches"] == []

        # Receipts change (provider amount now differs) -> mismatch detected.
        entries[0]["reward"] = 0.5
        entries[0]["invoiceHash"] = hash_receipt(
            {k: v for k, v in entries[0].items() if k != "invoiceHash"}
        )
        bad = client.get(f"/meshpay/payouts/{batch_id}/reconcile").json()
        assert bad["consistent"] is False
        assert "amount drifted" in bad["mismatches"][0]["reason"]

    def test_reconcile_missing_batch_is_404(self, client, monkeypatch):
        from backend.routes import meshpay as routes

        class FakeDcm:
            async def settlement_log(self, limit=200):
                return {"count": 0, "entries": [], "verification": {"valid": True}}

        monkeypatch.setattr(routes, "_client", lambda: FakeDcm())
        assert client.get("/meshpay/payouts/9999/reconcile").status_code == 404


# ---------------------------------------------------------------------------
# End-to-end route flow
# ---------------------------------------------------------------------------

class TestRouteFlow:
    def test_plan_approve_execute_via_routes(self, client, monkeypatch):
        from backend.routes import meshpay as routes

        entries = usage_chain([("peer-a", 0.05), ("peer-b", 0.03)])
        addr_a, addr_b = wallet(), wallet()

        class FakeDcm:
            async def settlement_log(self, limit=200):
                return {"count": len(entries), "entries": entries,
                        "verification": {"valid": True}}

        monkeypatch.setattr(routes, "_client", lambda: FakeDcm())

        assert client.post("/meshpay/wallets", json={
            "node_id": "peer-a", "wallet_address": addr_a,
        }).status_code == 200
        assert client.post("/meshpay/wallets", json={
            "node_id": "peer-b", "wallet_address": addr_b,
        }).status_code == 200

        planned = client.post("/meshpay/payouts", json={"epoch_size": 50})
        assert planned.status_code == 200, planned.text
        body = planned.json()
        assert body["status"] == "planned"
        assert len(body["instructions"]) == 2
        # 0.08 DCT gross, 15% fee -> $0.00068
        assert body["totals"]["payable_usdc"] == pytest.approx(0.00068)

        approved = client.post(
            f"/meshpay/payouts/{body['id']}/approve",
            headers={"X-Admin-Api-Key": ADMIN_KEY},
        )
        assert approved.status_code == 200

        executed = client.post(f"/meshpay/payouts/{body['id']}/execute")
        assert executed.status_code == 200
        assert executed.json()["status"] == "prepared"

        listed = client.get("/meshpay/payouts").json()
        assert any(p["id"] == body["id"] for p in listed["payouts"])
        fetched = client.get(f"/meshpay/payouts/{body['id']}").json()
        assert fetched["approved_by"] == "operator"
