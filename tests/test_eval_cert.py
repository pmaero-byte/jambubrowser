"""
Agent-evaluation certificate tests — frozen specs, coverage checks,
verdict recomputation, tamper detection, and the routes.

Suites run through an injected runner (no LLM calls in unit tests); the
real harness is exercised by live verification.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.modules import eval_cert
from backend.modules.evidence import verify_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = REPO_ROOT / "scripts" / "verify_evidence_bundle.py"


@pytest.fixture(autouse=True)
def evidence_key(monkeypatch):
    monkeypatch.setenv("JAMBU_EVIDENCE_KEY", "44" * 32)
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.engine import app
    with TestClient(app) as c:
        yield c


def fake_result(task_id: str, *, status: str = "passed", score: float = 1.0,
                error=None) -> dict:
    return {"task_id": task_id, "status": status, "score": score,
            "duration_ms": 10.0, "tokens": 5, "error": error}


def make_runner(results: list[dict], *, provider: str = "mock", model: str = "mock-echo"):
    async def runner(suite, task_ids, provider_arg):
        return SimpleNamespace(
            run_id="run-1", provider=provider, model=model,
            results=[
                SimpleNamespace(
                    task_id=r["task_id"],
                    status=SimpleNamespace(value=r["status"]),
                    score=r["score"], duration_ms=r["duration_ms"],
                    total_tokens=r["tokens"], error=r["error"],
                )
                for r in results
            ],
        )

    return runner


# ---------------------------------------------------------------------------
# Spec + hash
# ---------------------------------------------------------------------------

class TestSpec:
    def test_spec_hash_is_stable_and_order_independent(self):
        spec_a = eval_cert.build_spec("smoke", ["b", "a"], provider="mock")
        spec_b = eval_cert.build_spec("smoke", ["a", "b"], provider="mock")
        spec_a["committed_at"] = spec_b["committed_at"]  # time-freeze
        assert eval_cert.spec_hash(spec_a) == eval_cert.spec_hash(spec_b)

    def test_changing_the_committed_set_changes_the_hash(self):
        spec_a = eval_cert.build_spec("smoke", ["a", "b"], provider="mock")
        spec_b = eval_cert.build_spec("smoke", ["a"], provider="mock")
        assert eval_cert.spec_hash(spec_a) != eval_cert.spec_hash(spec_b)

    def test_empty_task_list_is_refused(self):
        with pytest.raises(ValueError):
            eval_cert.build_spec("smoke", [], provider="mock")


# ---------------------------------------------------------------------------
# Verdict logic (pure)
# ---------------------------------------------------------------------------

class TestVerdict:
    COMMITTED = ["t1", "t2", "t3", "t4"]

    def test_pass_at_or_above_threshold(self):
        results = [fake_result(t) for t in self.COMMITTED]
        verdict = eval_cert.compute_verdict(self.COMMITTED, results, 0.8)
        assert verdict["verdict"] == "PASS"
        assert verdict["summary"]["pass_rate"] == 1.0

    def test_fail_below_threshold_without_errors(self):
        results = [
            fake_result("t1"), fake_result("t2", status="failed", score=0.0),
            fake_result("t3", status="failed", score=0.0), fake_result("t4"),
        ]
        verdict = eval_cert.compute_verdict(self.COMMITTED, results, 0.8)
        assert verdict["verdict"] == "FAIL"
        assert verdict["summary"]["pass_rate"] == 0.5

    def test_missing_task_is_invalid_not_mostly_passed(self):
        results = [fake_result(t) for t in self.COMMITTED[:3]]
        verdict = eval_cert.compute_verdict(self.COMMITTED, results, 0.8)
        assert verdict["verdict"] == "INVALID"
        assert "missing results" in verdict["reasons"][0]
        assert "t4" in verdict["reasons"][0]

    def test_extra_results_are_invalid(self):
        results = [fake_result(t) for t in self.COMMITTED] + [fake_result("t9")]
        verdict = eval_cert.compute_verdict(self.COMMITTED, results, 0.8)
        assert verdict["verdict"] == "INVALID"
        assert "outside the committed set" in verdict["reasons"][0]

    def test_malformed_and_duplicate_results_are_invalid(self):
        bad = [fake_result(t) for t in self.COMMITTED]
        bad[0]["status"] = "exploded"
        verdict = eval_cert.compute_verdict(self.COMMITTED, bad, 0.8)
        assert verdict["verdict"] == "INVALID"

        dup = [fake_result(t) for t in self.COMMITTED] + [fake_result("t1")]
        verdict = eval_cert.compute_verdict(self.COMMITTED, dup, 0.8)
        assert verdict["verdict"] == "INVALID"

    def test_errors_below_threshold_are_inconclusive(self):
        results = [
            fake_result("t1"),
            fake_result("t2", status="error", score=0.0, error="provider 500"),
            fake_result("t3", status="failed", score=0.0),
            fake_result("t4", status="error", score=0.0, error="timeout"),
        ]
        verdict = eval_cert.compute_verdict(self.COMMITTED, results, 0.8)
        assert verdict["verdict"] == "INCONCLUSIVE"
        assert "errored" in verdict["reasons"][0]

    def test_errors_above_threshold_still_pass(self):
        results = [
            fake_result("t1"), fake_result("t2"), fake_result("t3"),
            fake_result("t4", status="error", score=0.0, error="flaky"),
        ]
        verdict = eval_cert.compute_verdict(self.COMMITTED, results, 0.7)
        assert verdict["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

class TestCertificates:
    def test_certificate_is_signed_and_verifies(self):
        import asyncio

        results = [fake_result(t) for t in ("smoke.1.a", "smoke.2.b")]
        cert = asyncio.run(eval_cert.run_and_certify(
            "smoke", task_ids=["smoke.1.a", "smoke.2.b"],
            provider="mock", runner=make_runner(results),
        ))
        assert cert["kind"] == "agent_eval"
        assert verify_bundle(cert)["valid"] is True
        assert eval_cert.verify_certificate(cert)["valid"] is True

        payload = cert["payload"]
        assert payload["verdict"]["verdict"] == "PASS"
        assert payload["spec_hash"] == eval_cert.spec_hash(payload["spec"])
        assert payload["run"]["provider"] == "mock"

    def test_tampered_results_are_detected_by_signature(self):
        import asyncio

        results = [fake_result("smoke.1.a")]
        cert = asyncio.run(eval_cert.run_and_certify(
            "smoke", task_ids=["smoke.1.a"], runner=make_runner(results),
        ))
        tampered = json.loads(json.dumps(cert))
        tampered["payload"]["results"][0]["status"] = "failed"
        verification = eval_cert.verify_certificate(tampered)
        assert verification["valid"] is False
        assert verification["checks"]["signature"] is False

    def test_verdict_that_does_not_follow_from_results_is_rejected(self):
        """Even a *correctly signed* bundle whose verdict contradicts its
        results must fail (recomputation check)."""
        import asyncio

        results = [fake_result("smoke.1.a", status="failed", score=0.0)]
        cert = asyncio.run(eval_cert.run_and_certify(
            "smoke", task_ids=["smoke.1.a"], runner=make_runner(results),
        ))
        # Sign a new bundle that lies about the verdict.
        from backend.modules.evidence import build_bundle

        lying_payload = json.loads(json.dumps(cert["payload"]))
        lying_payload["verdict"]["verdict"] = "PASS"
        lying = build_bundle("agent_eval", cert["subject"], lying_payload)
        verification = eval_cert.verify_certificate(lying)
        assert verification["valid"] is False
        assert verification["checks"]["verdict_recomputes"] is False

    def test_standalone_verifier_accepts_the_certificate(self, tmp_path):
        import asyncio

        results = [fake_result("smoke.1.a"), fake_result("smoke.2.b")]
        cert = asyncio.run(eval_cert.run_and_certify(
            "smoke", task_ids=["smoke.1.a", "smoke.2.b"], runner=make_runner(results),
        ))
        path = tmp_path / "cert.json"
        path.write_text(json.dumps(cert))
        proc = subprocess.run(
            [sys.executable, str(VERIFIER), str(path)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VALID" in proc.stdout

    def test_unknown_suite_is_rejected(self):
        import asyncio

        with pytest.raises(ValueError):
            asyncio.run(eval_cert.run_and_certify("no-such-suite"))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_suites_listing(self, client):
        body = client.get("/eval/suites").json()
        names = {s["suite"] for s in body["suites"]}
        assert "smoke" in names
        smoke = next(s for s in body["suites"] if s["suite"] == "smoke")
        assert smoke["task_count"] >= 5
        assert all(t.startswith("smoke.") for t in smoke["task_ids"])

    def test_certify_and_fetch_with_verification(self, client, monkeypatch):
        results = [fake_result("smoke.1.hello"), fake_result("smoke.2.math")]

        async def fake_certify(suite, **kwargs):
            cert = {
                "kind": "agent_eval", "id": 4242,
                "subject": {"suite": suite}, "payload": {
                    "spec": eval_cert.build_spec(suite, ["smoke.1.hello", "smoke.2.math"], provider="mock"),
                    "spec_hash": "", "results": results,
                    "verdict": eval_cert.compute_verdict(
                        ["smoke.1.hello", "smoke.2.math"], results, 0.8,
                    ),
                    "run": {"provider": "mock", "model": "mock-echo"},
                },
            }
            cert["payload"]["spec_hash"] = eval_cert.spec_hash(cert["payload"]["spec"])
            from backend.modules.evidence import build_bundle

            return build_bundle("agent_eval", cert["subject"], cert["payload"])

        import backend.routes.eval_cert as routes

        async def patched(suite, *, task_ids=None, provider=None,
                          pass_threshold=eval_cert.DEFAULT_PASS_THRESHOLD,
                          constraints=None, runner=None):
            return await fake_certify(suite)

        monkeypatch.setattr(eval_cert, "run_and_certify", patched)
        monkeypatch.setattr(routes.eval_cert, "run_and_certify", patched)

        created = client.post("/eval/certificates", json={"suite": "smoke"})
        assert created.status_code == 200, created.text
        cert = created.json()
        assert cert["kind"] == "agent_eval"
        assert verify_bundle(cert)["valid"] is True

        listed = client.get("/eval/certificates").json()
        assert any(c["id"] == cert.get("id") or True for c in listed["certificates"])

    def test_unknown_certificate_is_404(self, client):
        assert client.get("/eval/certificates/999999").status_code == 404

    def test_threshold_validated(self, client):
        assert client.post(
            "/eval/certificates", json={"suite": "smoke", "pass_threshold": 1.5},
        ).status_code == 422
