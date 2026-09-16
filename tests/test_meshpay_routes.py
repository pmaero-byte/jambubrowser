"""
Tests for the /meshpay/* routes — audit, proofs, anchoring, anchor history.

DCM is stubbed (no live node needed); the live contract is covered by
tests/test_dcm_integration.py and the MeshPay live check is run manually
with a booted node.
"""
from __future__ import annotations

import pytest

from backend.modules.meshpay import hash_receipt
from backend.modules.meshpay.merkle import verify_proof


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


def make_entry(kind: str, prev: str | None, **fields) -> dict:
    entry = {"kind": kind, "timestamp": 1700000000000, "prevInvoiceHash": prev}
    entry.update(fields)
    entry["invoiceHash"] = hash_receipt(entry)
    return entry


def usage_chain(n: int = 3) -> list[dict]:
    entries: list[dict] = []
    prev = None
    for i in range(n):
        entry = make_entry(
            "usage", prev, nodeId=f"peer-{i % 2}", cpu=100, bandwidth=200,
            reward=0.01 * (i + 1), payoutMultiplier=1.0, executionHash=f"e{i}",
            appHash="app", func="f", i32Args=None, stats=None,
            meteringSource="execMs-proxy",
        )
        entries.append(entry)
        prev = entry["invoiceHash"]
    return entries


class FakeDcmClient:
    def __init__(self, entries: list[dict], *, error=None, dcm_valid: bool | None = None):
        self.entries = entries
        self.error = error
        self.dcm_valid = dcm_valid

    async def settlement_log(self, limit: int = 50):
        if self.error:
            raise self.error
        window = self.entries[-limit:]
        return {
            "success": True,
            "count": len(self.entries),
            "entries": window,
            "verification": {
                "valid": self.dcm_valid if self.dcm_valid is not None else True,
                "entries": len(self.entries),
                "brokenAt": None,
                "totals": {},
            },
        }


def install_client(monkeypatch, fake: FakeDcmClient):
    import backend.routes.meshpay as meshpay_routes
    monkeypatch.setattr(meshpay_routes, "_client", lambda: fake)


class TestMeshPayConfigRoute:
    def test_config_is_safe_by_default(self, client):
        resp = client.get("/meshpay/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cluster"] == "mock"
        assert "mock" in body["transport"]
        assert body["dct_usd_rate"] == 0.01


class TestMeshPayAudit:
    def test_valid_chain_with_epochs_and_plan(self, client, monkeypatch):
        install_client(monkeypatch, FakeDcmClient(usage_chain(4)))
        resp = client.get("/meshpay/audit?limit=200&epoch_size=50")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verification"]["valid"] is True
        assert body["verification"]["checked"] == 4
        assert body["agreement"] is True
        assert len(body["epochs"]) == 1
        assert body["epochs"][0]["root"]
        assert body["payout"]["totals"]["grossDct"] == pytest.approx(0.10)
        assert body["window"]["returned"] == 4

    def test_tampered_chain_is_reported_and_disagreement_flagged(
        self, client, monkeypatch,
    ):
        entries = usage_chain(3)
        entries[1]["reward"] = 42.0  # tamper
        install_client(monkeypatch, FakeDcmClient(entries, dcm_valid=True))
        body = client.get("/meshpay/audit").json()
        assert body["verification"]["valid"] is False
        assert body["verification"]["broken_at"] == 1
        assert body["agreement"] is False  # we disagree with DCM's own verdict

    def test_truncated_window_is_flagged(self, client, monkeypatch):
        full = usage_chain(5)
        install_client(monkeypatch, FakeDcmClient(full))
        body = client.get("/meshpay/audit?limit=2").json()
        assert body["window"]["truncated"] is True
        assert body["verification"]["window_truncated"] is True

    def test_unreachable_node_502(self, client, monkeypatch):
        from backend.modules.dcm_client import DcmError
        install_client(monkeypatch, FakeDcmClient([], error=DcmError(0, "down")))
        resp = client.get("/meshpay/audit")
        assert resp.status_code == 502
        assert "decentracode/backend" in resp.json()["detail"]

    def test_limit_validated(self, client, monkeypatch):
        install_client(monkeypatch, FakeDcmClient(usage_chain(1)))
        assert client.get("/meshpay/audit?limit=0").status_code == 422
        assert client.get("/meshpay/audit?limit=999").status_code == 422

    def test_empty_log_is_a_clean_success(self, client, monkeypatch):
        install_client(monkeypatch, FakeDcmClient([]))
        body = client.get("/meshpay/audit").json()
        assert body["verification"]["valid"] is True
        assert body["epochs"] == []
        assert body["payout"] is None


class TestMeshPayReceiptProof:
    def test_proof_verifies_against_epoch_root(self, client, monkeypatch):
        entries = usage_chain(4)
        install_client(monkeypatch, FakeDcmClient(entries))
        body = client.get("/meshpay/receipts/2").json()
        assert body["position"] == 2
        assert body["invoice_hash"] == entries[2]["invoiceHash"]
        assert verify_proof(body["leaf"], body["proof"], body["root"]) is True

    def test_out_of_range_404(self, client, monkeypatch):
        install_client(monkeypatch, FakeDcmClient(usage_chain(2)))
        assert client.get("/meshpay/receipts/5").status_code == 404


class TestMeshPayAnchoring:
    def test_anchor_latest_epoch_mock_transport(self, client, monkeypatch):
        install_client(monkeypatch, FakeDcmClient(usage_chain(3)))
        resp = client.post("/meshpay/anchor", json={"limit": 200, "epoch_size": 2})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transport"] == "mock"
        assert body["signature"].startswith("mock:")
        assert body["epoch"]["index"] == 1  # latest epoch (-1 default)
        assert body["epoch"]["receipts"] == 1
        assert body["root"]
        assert body["explorer_url"] == ""  # mock has no explorer

    def test_anchor_requires_receipts(self, client, monkeypatch):
        install_client(monkeypatch, FakeDcmClient([]))
        resp = client.post("/meshpay/anchor", json={})
        assert resp.status_code == 422

    def test_anchor_epoch_index_validated(self, client, monkeypatch):
        install_client(monkeypatch, FakeDcmClient(usage_chain(2)))
        resp = client.post("/meshpay/anchor", json={"epoch_index": 9})
        assert resp.status_code == 422

    def test_anchor_history_re_verifies(self, client, monkeypatch):
        entries = usage_chain(3)
        install_client(monkeypatch, FakeDcmClient(entries))
        anchored = client.post(
            "/meshpay/anchor", json={"epoch_index": 0, "limit": 200},
        ).json()

        history = client.get("/meshpay/anchors?limit=10").json()
        assert history["count"] >= 1
        match = next(a for a in history["anchors"] if a["id"] == anchored["id"])
        assert match["status"] == "verified"
        assert match["matches"] is True
        assert match["current_root"] == anchored["root"]

        # Tamper with the receipt log: the anchored root no longer matches.
        entries[0]["reward"] = 999.0
        entries[0]["invoiceHash"] = hash_receipt(
            {k: v for k, v in entries[0].items() if k != "invoiceHash"}
        )
        history = client.get("/meshpay/anchors?limit=10").json()
        match = next(a for a in history["anchors"] if a["id"] == anchored["id"])
        assert match["status"] == "mismatch"

    def test_anchor_remembers_epoch_size_for_re_verification(self, client, monkeypatch):
        """Regression (found live): anchoring with a custom epoch size must
        still verify when the anchors endpoint uses the default size."""
        entries = usage_chain(5)
        install_client(monkeypatch, FakeDcmClient(entries))
        anchored = client.post(
            "/meshpay/anchor", json={"epoch_index": 1, "epoch_size": 2, "limit": 200},
        ).json()
        assert anchored["epoch_size"] == 2

        # Anchors endpoint called with the DEFAULT size — must still verify.
        history = client.get("/meshpay/anchors?limit=10").json()
        match = next(a for a in history["anchors"] if a["id"] == anchored["id"])
        assert match["status"] == "verified"
        assert match["verified_with_epoch_size"] == 2

    def test_anchor_epoch_field_relation(self, client, monkeypatch):
        """Anchored counts must equal the epoch window size it claims."""
        install_client(monkeypatch, FakeDcmClient(usage_chain(5)))
        body = client.post("/meshpay/anchor", json={"epoch_size": 2}).json()
        assert body["receipts"] == body["epoch"]["receipts"]
        assert body["epoch"]["to_index"] - body["epoch"]["from_index"] + 1 == body["epoch"]["receipts"]
