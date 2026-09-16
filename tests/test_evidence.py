"""
Evidence bundle tests — signing, verification, tamper detection, anchoring,
and the standalone verifier (run as a subprocess: it must work without
importing this project).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.modules import evidence
from backend.modules.evidence import (
    build_bundle,
    load_or_create_key,
    verify_bundle,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = REPO_ROOT / "scripts" / "verify_evidence_bundle.py"


@pytest.fixture(autouse=True)
def evidence_key_env(tmp_path, monkeypatch):
    """Deterministic key per test, never touching ~/.jambu."""
    seed = "11" * 32
    monkeypatch.setenv("JAMBU_EVIDENCE_KEY", seed)
    monkeypatch.delenv("JAMBU_EVIDENCE_KEY_PATH", raising=False)
    yield seed


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


def run_verifier(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERIFIER), str(path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

class TestKeys:
    def test_env_seed_is_used_and_fingerprint_is_stable(self, evidence_key_env):
        key = load_or_create_key()
        assert key.public_hex and len(bytes.fromhex(key.public_hex)) == 32
        assert key.fingerprint == load_or_create_key().fingerprint

    def test_file_key_is_generated_with_0600(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JAMBU_EVIDENCE_KEY", raising=False)
        key_path = tmp_path / "evidence.key"
        key = load_or_create_key(str(key_path))
        assert key_path.exists()
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600
        assert load_or_create_key(str(key_path)).public_hex == key.public_hex

    def test_key_route_reveals_no_seed(self, client, evidence_key_env):
        body = client.get("/evidence/key").json()
        assert body["algorithm"] == "ed25519"
        assert len(bytes.fromhex(body["public_key"])) == 32
        assert evidence_key_env not in json.dumps(body)


# ---------------------------------------------------------------------------
# Bundle build + verify
# ---------------------------------------------------------------------------

class TestBundleVerify:
    def _bundle(self) -> dict:
        return build_bundle(
            "audit_report",
            {"audit_id": 1, "url": "https://example.com"},
            {"findings": [{"severity": "high", "title": "XSS"}], "amount": 0.00001},
        )

    def test_roundtrip_is_valid(self):
        result = verify_bundle(self._bundle())
        assert result["valid"] is True
        assert all(result["checks"].values())

    def test_canonical_payload_is_embedded_and_js_serialized(self):
        bundle = self._bundle()
        # 1e-5 must serialize JS-style, proving the MeshPay serializer is used.
        assert "0.00001" in bundle["payload_canonical"]
        assert "1e-05" not in bundle["payload_canonical"]

    @pytest.mark.parametrize("mutation", [
        "payload", "payload_canonical", "created_at", "subject", "signature",
        "public_key", "statement_hash", "payload_hash",
    ])
    def test_any_tampering_is_detected(self, mutation):
        bundle = self._bundle()
        if mutation == "payload":
            bundle["payload"]["findings"][0]["severity"] = "low"
        elif mutation == "payload_canonical":
            bundle["payload_canonical"] = bundle["payload_canonical"].replace("high", "low!")
        elif mutation == "created_at":
            bundle["created_at"] = bundle["created_at"] + 1
        elif mutation == "subject":
            bundle["subject"]["audit_id"] = 999
        elif mutation == "signature":
            sig = bytearray.fromhex(bundle["signature"])
            sig[0] ^= 0xFF
            bundle["signature"] = sig.hex()
        elif mutation == "public_key":
            bundle["public_key"] = "22" * 32
        elif mutation == "statement_hash":
            bundle["statement_hash"] = "00" * 32
        elif mutation == "payload_hash":
            bundle["payload_hash"] = "00" * 32
        result = verify_bundle(bundle)
        assert result["valid"] is False, f"{mutation} was not detected"

    def test_standalone_verifier_accepts_and_rejects(self, tmp_path):
        bundle = build_bundle("x402_receipts", {"count": 1}, {"receipts": []})
        good = tmp_path / "good.json"
        good.write_text(json.dumps(bundle))

        proc = run_verifier(good)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VALID" in proc.stdout
        assert "PASS" in proc.stdout

        tampered = dict(bundle)
        tampered["subject"] = {"count": 999}
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(tampered))
        proc = run_verifier(bad)
        assert proc.returncode == 1
        assert "INVALID" in proc.stdout

    def test_standalone_verifier_exit_codes(self, tmp_path):
        missing = run_verifier(tmp_path / "nope.json")
        assert missing.returncode == 2


# ---------------------------------------------------------------------------
# Routes: audit, x402 receipts, dcm settlement, anchor
# ---------------------------------------------------------------------------

class TestEvidenceRoutes:
    def test_audit_bundle_roundtrip(self, client, monkeypatch):
        from backend.employees.base import Finding

        finding = Finding.from_dict({
            "severity": "high", "category": "security", "title": "Missing CSP",
            "description": "No Content-Security-Policy header",
            "employee": "Security Auditor",
        })
        import backend.routes.audit as audit_routes
        monkeypatch.setattr(
            audit_routes, "_load_findings_for_audit",
            lambda audit_id: ("https://example.com", [finding], {"total": 1}),
        )

        created = client.post("/evidence/audit/1")
        assert created.status_code == 200, created.text
        bundle = created.json()
        assert bundle["kind"] == "audit_report"
        assert verify_bundle(bundle)["valid"] is True

        fetched = client.get(f"/evidence/bundles/{bundle['id']}").json()
        assert fetched["payload"]["findings"][0]["title"] == "Missing CSP"
        assert fetched["verification"]["valid"] is True

    def test_audit_bundle_404_when_missing(self, client, monkeypatch):
        import backend.routes.audit as audit_routes
        from fastapi import HTTPException

        def raise_404(audit_id):
            raise HTTPException(status_code=404, detail="not found")

        monkeypatch.setattr(audit_routes, "_load_findings_for_audit", raise_404)
        assert client.post("/evidence/audit/999").status_code == 404

    def test_x402_receipts_bundle_contains_the_merkle_root(self, client):
        from backend.modules import x402

        x402.record_receipt(
            resource="http://t/audit/quick",
            requirements={"amount": "20000", "asset": "0xusdc", "network": "eip155:84532", "payTo": "0xpay"},
            payer="0xpayer", transaction="mocktx:1", nonce="mock-ev-1",
            status="settled", facilitator_mode="mock",
        )
        resp = client.post("/evidence/x402-receipts?limit=50")
        assert resp.status_code == 200, resp.text
        bundle = resp.json()
        root = x402.receipts_root(50)["root"]
        assert bundle["payload"]["merkle_root"] == root
        assert bundle["subject"]["merkle_root"] == root
        assert verify_bundle(bundle)["valid"] is True

    def test_dcm_settlement_bundle_signs_the_verdict(self, client, monkeypatch):
        from backend.modules.meshpay import hash_receipt

        def make_entry(kind, prev, **fields):
            entry = {"kind": kind, "timestamp": 1700000000000, "prevInvoiceHash": prev}
            entry.update(fields)
            entry["invoiceHash"] = hash_receipt(entry)
            return entry

        e1 = make_entry("usage", None, nodeId="peer-a", cpu=1, bandwidth=1,
                        reward=0.01, payoutMultiplier=1.0, executionHash=None,
                        appHash=None, func=None, i32Args=None, stats=None,
                        meteringSource="execMs-proxy")

        async def fake_log(self, limit=50):
            return {"count": 1, "entries": [e1],
                    "verification": {"valid": True, "entries": 1, "brokenAt": None}}

        from backend.modules.dcm_client import DcmClient
        monkeypatch.setattr(DcmClient, "settlement_log", fake_log)

        resp = client.post("/evidence/dcm-settlement?limit=50")
        assert resp.status_code == 200, resp.text
        bundle = resp.json()
        assert bundle["kind"] == "dcm_settlement"
        assert bundle["payload"]["verdict"]["valid"] is True
        assert bundle["subject"]["chain_valid"] is True
        assert verify_bundle(bundle)["valid"] is True

    def test_anchor_bundle_mock_transport_and_conflict(self, client, monkeypatch):
        bundle = client.post("/evidence/x402-receipts", json={}).json()
        resp = client.post("/evidence/anchor", json={"bundle_id": bundle["id"]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transport"] == "mock"
        assert body["signature"].startswith("mock:")
        assert body["payload_hash"] == bundle["payload_hash"]

        again = client.post("/evidence/anchor", json={"bundle_id": bundle["id"]})
        assert again.status_code == 409

        listed = client.get("/evidence/bundles?limit=10").json()
        row = next(b for b in listed["bundles"] if b["id"] == bundle["id"])
        assert row["anchor_transport"] == "mock"

    def test_verify_endpoint_rejects_tampered_bundle(self, client):
        bundle = client.post("/evidence/x402-receipts", json={}).json()
        bundle["signature"] = "00" * 64
        resp = client.post("/evidence/verify", json={"bundle": bundle})
        assert resp.status_code == 200
        assert resp.json()["valid"] is False
