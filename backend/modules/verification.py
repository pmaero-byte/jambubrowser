"""
Verification tiers for paid compute.

The DePIN verification ladder, implemented where it matters for this
engine: *sampled* redundancy (cheap, like IMMACULATE's <1% overhead
audits), known-answer canaries, and a declared policy that maps value at
risk to a required tier.

| Tier | Mechanism | Catches |
|---|---|---|
| ``SIGNED`` | hash-chained receipts (MeshPay) | tampering after the fact |
| ``CANARY`` | known-answer tasks with must-contain checks | lazy/broken workers |
| ``REDUNDANT`` | a second executor runs the same task; outputs compared with a tolerance | substituted models, truncated output, silent divergence |
| ``ATTESTED`` | TEE/hardware attestation | (not implemented — no hardware lane here) |

Policy (``required_tier``): small jobs are SIGNED, mid-value jobs get
canaries, high-value jobs must run REDUNDANT — thresholds are env-tunable
and the policy payload is explicit about what it does and does not prove.

Executors are pluggable: a "worker" is anything with an async ``run(text)``
returning an output. ``faulty-echo`` exists **only as a fault-injection
double** so the detection path is demonstrable; it is clearly labelled.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend.modules.meshpay import js_dumps

log = logging.getLogger("jambu.verification")

TIERS = ("SIGNED", "CANARY", "REDUNDANT", "ATTESTED")
DEFAULT_TOLERANCE = 0.85          # similarity below this = MISMATCH
DEFAULT_REDUNDANT_ABOVE_USDC = 1.0
DEFAULT_CANARY_ABOVE_USDC = 0.01


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def required_tier(price_usdc: float) -> str:
    redundant_above = float(
        os.environ.get("JAMBU_VERIFY_REDUNDANT_ABOVE_USDC", DEFAULT_REDUNDANT_ABOVE_USDC)
    )
    canary_above = float(
        os.environ.get("JAMBU_VERIFY_CANARY_ABOVE_USDC", DEFAULT_CANARY_ABOVE_USDC)
    )
    if price_usdc >= redundant_above:
        return "REDUNDANT"
    if price_usdc >= canary_above:
        return "CANARY"
    return "SIGNED"


def policy() -> dict:
    return {
        "tiers": list(TIERS),
        "thresholds_usdc": {
            "canary_above": float(
                os.environ.get("JAMBU_VERIFY_CANARY_ABOVE_USDC", DEFAULT_CANARY_ABOVE_USDC)
            ),
            "redundant_above": float(
                os.environ.get("JAMBU_VERIFY_REDUNDANT_ABOVE_USDC", DEFAULT_REDUNDANT_ABOVE_USDC)
            ),
        },
        "tolerance": DEFAULT_TOLERANCE,
        "attested_lane": "not implemented (no TEE hardware integration)",
        "note": (
            "Policy declares the required tier per value; it does not itself "
            "verify anything. Redundancy samples are explicit work, canaries "
            "are cheap probes, and both only catch what they compare."
        ),
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_exact(primary: str, replica: str) -> tuple[bool, float]:
    return primary == replica, 1.0 if primary == replica else 0.0


def compare_similarity(primary: str, replica: str) -> tuple[bool, float]:
    ratio = difflib.SequenceMatcher(None, primary or "", replica or "").ratio()
    return ratio >= DEFAULT_TOLERANCE, round(ratio, 4)


COMPARATORS: dict[str, Callable[[str, str], tuple[bool, float]]] = {
    "exact": compare_exact,
    "similarity": compare_similarity,
}


# ---------------------------------------------------------------------------
# Executors ("workers")
# ---------------------------------------------------------------------------

@dataclass
class ExecutorResult:
    worker_id: str
    output: str
    execution_hash: str
    duration_ms: float
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "output": self.output,
            "execution_hash": self.execution_hash,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def _seal(worker_id: str, output: str) -> str:
    return hashlib.sha256(js_dumps({
        "worker": worker_id, "output": output,
    }).encode("utf-8")).hexdigest()


class Executor:
    """A worker: async run(text) -> output string. Subclass or register a fn."""

    id = "base"

    async def run(self, text: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class EchoExecutor(Executor):
    id = "echo"

    async def run(self, text: str) -> str:
        return f"echo:{text}"


class MockLlmExecutor(Executor):
    id = "mock-llm"

    async def run(self, text: str) -> str:
        from backend.llm.base import ChatMessage, Role
        from backend.llm.registry import get_registry

        response = await get_registry().chat(
            [ChatMessage(role=Role.USER, content=text)],
            provider="mock", max_tokens=128,
        )
        return response.content


class FaultyEchoExecutor(Executor):
    """Fault-injection double — NOT a real worker.

    Truncates its output so the redundancy comparator has something to
    detect. Used in tests and the live demo to prove mismatch handling.
    """

    id = "faulty-echo"

    async def run(self, text: str) -> str:
        return f"echo:{text}"[: max(1, len(text) // 2)]


EXECUTORS: dict[str, Executor] = {
    ex.id: ex for ex in (EchoExecutor(), MockLlmExecutor(), FaultyEchoExecutor())
}


def register_executor(executor: Executor) -> None:
    EXECUTORS[executor.id] = executor


async def run_on(worker_id: str, text: str) -> ExecutorResult:
    executor = EXECUTORS.get(worker_id)
    if executor is None:
        return ExecutorResult(worker_id, "", "", 0.0, error=f"unknown worker: {worker_id}")
    started = time.monotonic()
    try:
        output = await executor.run(text)
        duration = (time.monotonic() - started) * 1000
        return ExecutorResult(worker_id, output, _seal(worker_id, output), round(duration, 2))
    except Exception as e:
        duration = (time.monotonic() - started) * 1000
        log.warning("worker %s failed: %s", worker_id, e)
        return ExecutorResult(worker_id, "", "", round(duration, 2), error=str(e))


# ---------------------------------------------------------------------------
# Canaries (known-answer probes)
# ---------------------------------------------------------------------------

CANARY_TASKS = [
    {"id": "canary.alpha", "prompt": "canary-7f3a9", "must_contain": "canary-7f3a9"},
    {"id": "canary.beta", "prompt": "reply with the token K9X2", "must_contain": "K9X2"},
]


async def run_canary(worker_id: str) -> dict:
    results = []
    for task in CANARY_TASKS:
        result = await run_on(worker_id, task["prompt"])
        passed = (
            result.error is None
            and task["must_contain"] in (result.output or "")
        )
        results.append({
            "canary_id": task["id"],
            "passed": passed,
            "detail": "output missing the required token" if not passed else "ok",
            "execution_hash": result.execution_hash,
            "error": result.error,
        })
    verdict = "PASS" if all(r["passed"] for r in results) else "FAIL"
    record_verdict(
        kind="canary", worker_id=worker_id, verdict=verdict, tier="CANARY",
        detail={"canaries": results},
    )
    return {"worker_id": worker_id, "verdict": verdict, "canaries": results}


# ---------------------------------------------------------------------------
# Redundant execution
# ---------------------------------------------------------------------------

async def run_redundant(
    text: str,
    *,
    primary: str = "echo",
    replica: str = "echo",
    comparator: str = "similarity",
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict:
    if comparator not in COMPARATORS:
        raise ValueError(f"unknown comparator {comparator!r}; have {sorted(COMPARATORS)}")
    if primary not in EXECUTORS or replica not in EXECUTORS:
        raise ValueError(f"unknown worker; have {sorted(EXECUTORS)}")

    primary_result = await run_on(primary, text)
    replica_result = await run_on(replica, text)

    if primary_result.error or replica_result.error:
        verdict, agreement = "ERROR", None
    else:
        okay, agreement = COMPARATORS[comparator](
            primary_result.output, replica_result.output,
        )
        if comparator == "similarity":
            okay = agreement >= tolerance
        verdict = "MATCH" if okay else "MISMATCH"

    record = {
        "task_id": uuid.uuid4().hex,
        "verdict": verdict,
        "tier": "REDUNDANT",
        "comparator": comparator,
        "tolerance": tolerance,
        "agreement": agreement,
        "primary": primary_result.to_dict(),
        "replica": replica_result.to_dict(),
        "created_at": time.time(),
    }
    record_verdict(
        kind="redundant", worker_id=primary, verdict=verdict, tier="REDUNDANT",
        detail=record,
    )
    return record


# ---------------------------------------------------------------------------
# Verdict store + scorecards
# ---------------------------------------------------------------------------

def record_verdict(*, kind: str, worker_id: str, verdict: str, tier: str,
                   detail: dict) -> None:
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO worker_verdicts
                (kind, worker_id, verdict, tier, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (kind, worker_id, verdict, tier, js_dumps(detail), time.time()),
        )
        conn.commit()


def list_verdicts(limit: int = 50, worker_id: Optional[str] = None) -> list[dict]:
    from backend.core.database import get_db

    query = "SELECT * FROM worker_verdicts"
    params: list[Any] = []
    if worker_id:
        query += " WHERE worker_id = ?"
        params.append(worker_id)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        try:
            record["detail"] = json.loads(record.pop("detail_json") or "{}")
        except ValueError:
            record["detail"] = {}
        out.append(record)
    return out


def worker_report(worker_id: Optional[str] = None) -> dict:
    """Scorecards: canary pass rate + redundancy agreement per worker."""
    verdicts = list_verdicts(limit=1000)
    workers: dict[str, dict] = {}
    for v in verdicts:
        if worker_id and v["worker_id"] != worker_id:
            continue
        score = workers.setdefault(v["worker_id"], {
            "worker_id": v["worker_id"],
            "canaries_run": 0, "canaries_passed": 0,
            "redundancy_runs": 0, "redundancy_matches": 0,
            "mismatches": 0, "errors": 0, "last_verdict_at": None,
        })
        score["last_verdict_at"] = v["created_at"]
        if v["kind"] == "canary":
            score["canaries_run"] += 1
            if v["verdict"] == "PASS":
                score["canaries_passed"] += 1
        elif v["kind"] == "redundant":
            score["redundancy_runs"] += 1
            if v["verdict"] == "MATCH":
                score["redundancy_matches"] += 1
            elif v["verdict"] == "MISMATCH":
                score["mismatches"] += 1
            else:
                score["errors"] += 1
    for score in workers.values():
        score["canary_pass_rate"] = (
            round(score["canaries_passed"] / score["canaries_run"], 4)
            if score["canaries_run"] else None
        )
        score["redundancy_agreement_rate"] = (
            round(score["redundancy_matches"] / score["redundancy_runs"], 4)
            if score["redundancy_runs"] else None
        )
    if worker_id:
        return workers.get(worker_id, {
            "worker_id": worker_id, "canaries_run": 0, "canaries_passed": 0,
            "redundancy_runs": 0, "redundancy_matches": 0,
            "mismatches": 0, "errors": 0, "last_verdict_at": None,
            "canary_pass_rate": None, "redundancy_agreement_rate": None,
        })
    return {"workers": sorted(workers.values(), key=lambda w: w["worker_id"])}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def verification_bundle(limit: int = 200) -> dict:
    """Sign the verdict window into an evidence bundle (kind compute_verification)."""
    from backend.modules.evidence import build_bundle

    verdicts = list_verdicts(limit=limit)
    payload = {
        "window_limit": limit,
        "policy": policy(),
        "workers": worker_report(),
        "verdicts": verdicts,
    }
    return build_bundle(
        "compute_verification",
        {
            "verdicts": len(verdicts),
            "mismatches": sum(1 for v in verdicts if v["verdict"] == "MISMATCH"),
        },
        payload,
    )
