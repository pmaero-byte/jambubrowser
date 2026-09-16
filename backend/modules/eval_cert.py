"""
Agent-evaluation certificates — signed, coverage-checked eval receipts.

Benchmarks are theater unless you can show *what was committed, what ran,
and what the score means*. This module wraps the existing eval harness
(``backend/eval``: smoke, gaia, webarena_mini, memory, privacy, …) in the
ClaimReceipt discipline:

1. **Freeze the spec before running.** The suite's task list, scoring rule,
   provider/model under test and harness constraints are canonicalized and
   hashed (``spec_hash``) *before* any task executes. Dropping a failed task
   afterwards changes coverage and is detected.
2. **Coverage is a first-class check.** Every committed task must appear
   exactly once in the results. Missing/extra/malformed results make the
   certificate ``INVALID`` — not silently "mostly passed".
3. **Verdicts are recomputable.** ``PASS`` if the pass rate meets the
   threshold; ``FAIL`` otherwise; ``INCONCLUSIVE`` when harness errors make
   the score untrustworthy (errors present and below threshold).
4. **The certificate is an evidence bundle** (E3): Ed25519-signed, verified
   by ``scripts/verify_evidence_bundle.py`` with no project imports, and
   anchorable through MeshPay.

Verification also re-derives the verdict from the embedded results, so even
a correctly signed certificate whose verdict doesn't follow from its own
data is rejected.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Callable, Optional

from backend.modules.evidence import build_bundle, save_bundle, verify_bundle
from backend.modules.meshpay import js_dumps

log = logging.getLogger("jambu.eval_cert")

SPEC_VERSION = 1
DEFAULT_PASS_THRESHOLD = 0.8
VALID_STATUSES = {"passed", "failed", "error", "timeout", "skipped"}


# ---------------------------------------------------------------------------
# Spec + hashing
# ---------------------------------------------------------------------------

def build_spec(
    suite: str,
    task_ids: list[str],
    *,
    provider: Optional[str],
    model: Optional[str] = None,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    constraints: Optional[dict] = None,
) -> dict:
    """The frozen experiment definition (hashed before any task runs)."""
    if not task_ids:
        raise ValueError("task_ids must be non-empty (nothing committed → nothing to certify)")
    return {
        "spec_version": SPEC_VERSION,
        "suite": suite,
        "task_ids": sorted(task_ids),
        "scoring": {
            "rule": "substring/expected-match per task (harness default)",
            "pass_threshold": pass_threshold,
        },
        "subject": {
            "provider": provider or "auto",
            "model": model or "default",
        },
        "constraints": constraints or {},
        "committed_at": time.time(),
    }


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(js_dumps(spec).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Verdict (pure — recomputable by a verifier)
# ---------------------------------------------------------------------------

def compute_verdict(
    committed: list[str], results: list[dict], pass_threshold: float,
) -> dict:
    """Coverage + scoring. Deterministic: same inputs → same verdict."""
    by_id: dict[str, dict] = {}
    duplicates: list[str] = []
    for r in results:
        task_id = r.get("task_id")
        if task_id in by_id:
            duplicates.append(task_id)
        by_id[task_id] = r

    missing = sorted(t for t in committed if t not in by_id)
    extra = sorted(t for t in by_id if t not in committed)
    malformed = sorted(
        str(r.get("task_id")) for r in results
        if r.get("status") not in VALID_STATUSES
    )

    counts = {"passed": 0, "failed": 0, "error": 0, "timeout": 0, "skipped": 0}
    for r in results:
        status = r.get("status")
        if status in counts:
            counts[status] += 1
    total = len(committed)
    pass_rate = (counts["passed"] / total) if total else 0.0
    summary = {
        "committed": total,
        "reported": len(results),
        "pass_rate": round(pass_rate, 4),
        **counts,
    }

    if missing or extra or malformed or duplicates:
        reasons = []
        if missing:
            reasons.append(f"missing results: {missing}")
        if extra:
            reasons.append(f"results outside the committed set: {extra}")
        if malformed:
            reasons.append(f"malformed results: {malformed}")
        if duplicates:
            reasons.append(f"duplicate results: {sorted(set(duplicates))}")
        return {"verdict": "INVALID", "reasons": reasons, "summary": summary}

    if pass_rate >= pass_threshold:
        return {"verdict": "PASS", "reasons": [], "summary": summary}
    if counts["error"] > 0:
        return {
            "verdict": "INCONCLUSIVE",
            "reasons": [
                f"{counts['error']} task(s) errored and the pass rate "
                f"({pass_rate:.2%}) is below the {pass_threshold:.0%} threshold — "
                "the score cannot be trusted"
            ],
            "summary": summary,
        }
    return {"verdict": "FAIL", "reasons": [], "summary": summary}


# ---------------------------------------------------------------------------
# Running + certifying
# ---------------------------------------------------------------------------

async def _harness_runner(suite: str, task_ids: list[str], provider: Optional[str]):
    import backend.eval.tasks  # noqa: F401 — registers task suites
    from backend.eval.harness import Harness

    return await Harness(provider=provider or None).run_suite(
        suite, task_ids=task_ids,
    )


def _result_records(suite_result) -> list[dict]:
    return [
        {
            "task_id": r.task_id,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "score": round(float(r.score or 0.0), 4),
            "duration_ms": round(float(r.duration_ms or 0.0), 1),
            "tokens": int(r.total_tokens or 0),
            "error": (r.error or None),
        }
        for r in suite_result.results
    ]


async def run_and_certify(
    suite: str,
    *,
    task_ids: Optional[list[str]] = None,
    provider: Optional[str] = None,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    constraints: Optional[dict] = None,
    runner: Optional[Callable] = None,
) -> dict:
    """Freeze a spec, run the suite, judge coverage + score, sign a bundle."""
    import backend.eval.tasks  # noqa: F401 — registers task suites
    from backend.eval.harness import list_suites, list_tasks

    if suite not in list_suites():
        raise ValueError(f"unknown suite {suite!r}; available: {list_suites()}")

    committed = sorted(task_ids) if task_ids else sorted(
        t.id for t in list_tasks(suite=suite)
    )
    spec = build_spec(
        suite, committed, provider=provider, pass_threshold=pass_threshold,
        constraints=constraints,
    )
    frozen_hash = spec_hash(spec)

    started = time.time()
    suite_result = await (runner or _harness_runner)(suite, committed, provider)
    duration = time.time() - started

    results = _result_records(suite_result)
    verdict = compute_verdict(committed, results, pass_threshold)

    payload = {
        "spec": spec,
        "spec_hash": frozen_hash,
        "results": results,
        "verdict": verdict,
        "run": {
            "started_at": started,
            "duration_seconds": round(duration, 2),
            "provider": str(getattr(suite_result, "provider", provider or "auto")),
            "model": str(getattr(suite_result, "model", "default")),
            "run_id": str(getattr(suite_result, "run_id", "")),
        },
    }
    bundle = build_bundle(
        "agent_eval",
        {
            "suite": suite,
            "verdict": verdict["verdict"],
            "pass_rate": verdict["summary"]["pass_rate"],
            "spec_hash": frozen_hash,
        },
        payload,
    )
    return save_bundle(bundle)


# ---------------------------------------------------------------------------
# Verification (signature + recomputation)
# ---------------------------------------------------------------------------

def verify_certificate(bundle: dict) -> dict:
    """Verify the signature *and* that the verdict follows from the data."""
    checks: dict[str, Any] = {}
    signature = verify_bundle(bundle)
    checks["signature"] = signature["valid"]

    payload = bundle.get("payload") or {}
    spec = payload.get("spec") or {}
    results = payload.get("results") or []
    verdict = payload.get("verdict") or {}

    recomputed_hash = spec_hash(spec) if spec else None
    checks["spec_hash"] = bool(recomputed_hash) and recomputed_hash == payload.get("spec_hash")

    threshold = (spec.get("scoring") or {}).get("pass_threshold", DEFAULT_PASS_THRESHOLD)
    recomputed = compute_verdict(spec.get("task_ids") or [], results, threshold)
    checks["verdict_recomputes"] = (
        recomputed["verdict"] == verdict.get("verdict")
        and recomputed["summary"] == verdict.get("summary")
    )
    checks["kind"] = bundle.get("kind") == "agent_eval"

    valid = all(checks.values())
    return {
        "valid": valid,
        "checks": checks,
        "reason": None if valid else next(
            (k for k, v in checks.items() if not v), "invalid"
        ),
        "recomputed": recomputed,
        "signature": signature,
    }


def list_certificates(limit: int = 50) -> list[dict]:
    from backend.modules.evidence import list_bundles

    return [b for b in list_bundles(limit) if b.get("kind") == "agent_eval"]


def get_certificate(bundle_id: int) -> Optional[dict]:
    from backend.modules.evidence import get_bundle

    bundle = get_bundle(bundle_id)
    if bundle is None or bundle.get("kind") != "agent_eval":
        return None
    return bundle
