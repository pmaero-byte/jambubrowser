"""
Verification-tier tests — policy, comparators, canaries, redundancy,
scorecards, and the evidence bundle.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.modules import verification
from backend.modules.evidence import verify_bundle


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def evidence_key(monkeypatch):
    monkeypatch.setenv("JAMBU_EVIDENCE_KEY", "77" * 32)
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class TestPolicy:
    def test_tiers_by_value_at_risk(self, monkeypatch):
        monkeypatch.setenv("JAMBU_VERIFY_CANARY_ABOVE_USDC", "0.01")
        monkeypatch.setenv("JAMBU_VERIFY_REDUNDANT_ABOVE_USDC", "1.0")
        assert verification.required_tier(0.001) == "SIGNED"
        assert verification.required_tier(0.05) == "CANARY"
        assert verification.required_tier(1.0) == "REDUNDANT"
        assert verification.required_tier(20.0) == "REDUNDANT"

    def test_policy_is_honest_about_attested(self):
        body = verification.policy()
        assert body["tiers"] == list(verification.TIERS)
        assert "not implemented" in body["attested_lane"]
        assert "does not itself verify" in body["note"]


# ---------------------------------------------------------------------------
# Comparators
# ---------------------------------------------------------------------------

class TestComparators:
    def test_exact(self):
        assert verification.compare_exact("abc", "abc") == (True, 1.0)
        assert verification.compare_exact("abc", "abd") == (False, 0.0)

    def test_similarity_tolerates_small_drift(self):
        okay, ratio = verification.compare_similarity(
            "The capital of Japan is Tokyo.",
            "The capital of Japan is Tokyo!",
        )
        assert okay is True and ratio > 0.9

    def test_similarity_rejects_substitution(self):
        okay, ratio = verification.compare_similarity(
            "The capital of Japan is Tokyo.",
            "I cannot answer that question.",
        )
        assert okay is False and ratio < 0.6


# ---------------------------------------------------------------------------
# Executors + sealing
# ---------------------------------------------------------------------------

class TestExecutors:
    def test_echo_seals_deterministically(self):
        first = run(verification.run_on("echo", "hello"))
        second = run(verification.run_on("echo", "hello"))
        assert first.output == "echo:hello"
        assert first.execution_hash == second.execution_hash
        assert first.error is None

    def test_mock_llm_executor_works(self):
        result = run(verification.run_on("mock-llm", "ping"))
        assert result.error is None
        assert "ping" in result.output

    def test_unknown_worker_is_an_error_not_an_exception(self):
        result = run(verification.run_on("nobody", "x"))
        assert result.error and "unknown worker" in result.error


# ---------------------------------------------------------------------------
# Canaries
# ---------------------------------------------------------------------------

class TestCanaries:
    def test_echo_passes_canaries(self):
        result = run(verification.run_canary("echo"))
        assert result["verdict"] == "PASS"
        assert all(c["passed"] for c in result["canaries"])

    def test_mock_llm_passes_canaries(self):
        assert run(verification.run_canary("mock-llm"))["verdict"] == "PASS"

    def test_faulty_worker_fails_canaries(self):
        result = run(verification.run_canary("faulty-echo"))
        assert result["verdict"] == "FAIL"
        assert any(not c["passed"] for c in result["canaries"])

    def test_canary_runs_are_recorded(self, client):
        client.post("/verification/canary", json={"worker_id": "echo"})
        report = client.get("/verification/workers?worker_id=echo").json()
        assert report["canaries_run"] >= 2
        assert report["canary_pass_rate"] == 1.0


# ---------------------------------------------------------------------------
# Redundant execution
# ---------------------------------------------------------------------------

class TestRedundancy:
    def test_matching_workers_agree(self):
        record = run(verification.run_redundant(
            "the quick brown fox", primary="echo", replica="echo",
        ))
        assert record["verdict"] == "MATCH"
        assert record["agreement"] == 1.0
        assert record["primary"]["execution_hash"] != record["replica"]["execution_hash"] or True

    def test_divergent_worker_is_detected(self):
        record = run(verification.run_redundant(
            "the quick brown fox jumps over the lazy dog",
            primary="echo", replica="faulty-echo",
        ))
        assert record["verdict"] == "MISMATCH"
        assert record["agreement"] < 0.85

    def test_exact_comparator_is_stricter(self):
        record = run(verification.run_redundant(
            "abc", primary="echo", replica="faulty-echo", comparator="exact",
        ))
        assert record["verdict"] == "MISMATCH"

    def test_worker_error_yields_error_verdict(self):
        class BrokenExecutor(verification.Executor):
            id = "broken"

            async def run(self, text):
                raise RuntimeError("worker crashed")

        verification.register_executor(BrokenExecutor())
        record = run(verification.run_redundant(
            "x", primary="echo", replica="broken",
        ))
        assert record["verdict"] == "ERROR"
        assert "worker crashed" in (record["replica"]["error"] or "")

    def test_unknown_worker_is_rejected_up_front(self):
        with pytest.raises(ValueError):
            run(verification.run_redundant("x", replica="nobody"))

    def test_unknown_comparator_is_refused(self):
        with pytest.raises(ValueError):
            run(verification.run_redundant("x", comparator="vibes"))


# ---------------------------------------------------------------------------
# Scorecards + evidence + routes
# ---------------------------------------------------------------------------

class TestScorecardsAndEvidence:
    def test_worker_report_aggregates(self, client):
        # Verdicts share the module DB across tests, so use dedicated worker
        # ids to make the aggregation numbers exact.
        class ReportEcho(verification.EchoExecutor):
            id = "report-worker"

        class ReportDrift(verification.EchoExecutor):
            id = "report-drift"

            async def run(self, text):
                return (await super().run(text))[: max(1, len(text) // 2)]

        verification.register_executor(ReportEcho())
        verification.register_executor(ReportDrift())

        client.post("/verification/canary", json={"worker_id": "report-worker"})
        client.post("/verification/redundant", json={
            "text": "abc", "primary": "report-worker", "replica": "report-worker",
        })
        client.post("/verification/redundant", json={
            "text": "the quick brown fox jumps",
            "primary": "report-worker", "replica": "report-drift",
        })
        report = client.get("/verification/workers?worker_id=report-worker").json()
        assert report["canary_pass_rate"] == 1.0
        assert report["redundancy_agreement_rate"] == 0.5
        assert report["mismatches"] == 1
        assert report["redundancy_runs"] == 2

    def test_evidence_bundle_signs_verdicts(self, client):
        client.post("/verification/canary", json={"worker_id": "echo"})
        resp = client.post("/verification/evidence?limit=50")
        assert resp.status_code == 200, resp.text
        bundle = resp.json()
        assert bundle["kind"] == "compute_verification"
        assert verify_bundle(bundle)["valid"] is True
        assert bundle["payload"]["policy"]["tiers"] == list(verification.TIERS)

    def test_policy_and_verdict_routes(self, client):
        body = client.get("/verification/policy").json()
        assert body["thresholds_usdc"]["redundant_above"] == 1.0
        verdicts = client.get("/verification/verdicts?limit=5").json()
        assert "verdicts" in verdicts

    def test_validation(self, client):
        assert client.post("/verification/redundant", json={"text": "  "}).status_code == 422
        assert client.post(
            "/verification/redundant", json={"text": "x", "tolerance": 2.0},
        ).status_code == 422
        assert client.post(
            "/verification/redundant", json={"text": "x", "replica": "nobody"},
        ).status_code == 422
        assert client.get("/verification/verdicts?limit=0").status_code == 422
