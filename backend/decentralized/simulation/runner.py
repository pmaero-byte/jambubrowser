"""Pricing, replica verification, and dispatch.

`run_job` is the whole scheduling story in one function: pick candidate
nodes by health and reputation, fan the spec out, verify the replicas
against each other, record the verdict, and return the outcome. It is
long because those steps share state, not because they are complex —
each one is a function here.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from typing import Any, Optional

from backend.decentralized.meshpay import js_dumps
from backend.decentralized.simulation import executors
from backend.decentralized.simulation.executors import (
    SimulationExecutor,
    available_nodes,
    default_registry,
)
from backend.decentralized.simulation.health import (
    _candidate_order,
    _publish_verdicts,
    _record_outcome,
    node_health,
    reputation,
)
from backend.decentralized.simulation.types import (
    STATUS_FAILED,
    STATUS_QUARANTINED,
    STATUS_SETTLED,
    ExecutorError,
    SimulationConfig,
    SimulationError,
    SimulationSpec,
)
from backend.decentralized.verification import (
    deviation,
    parse_numeric,
    required_tier,
)

# One logger name for the whole subsystem, so a log filter on
# "jambu.simulation" still catches every module after the split.
log = logging.getLogger("jambu.simulation")


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def quote(spec: SimulationSpec, *, replicates: int = 1) -> dict:
    """Price a job in DCT, convert to USD, and pick the required tier.

    This is the bridge the old code was missing: verification tiers were
    selected by ``required_tier(price_usdc)`` while the mesh meters in DCT,
    so a simulation's value never reached the policy that decides whether it
    gets replicated. The tier a quote reports is the tier the scheduler
    actually enforces (``REDUNDANT`` forces at least two replicas).
    """
    cfg = SimulationConfig.from_env()
    if not 1 <= replicates <= cfg.max_replicas:
        raise SimulationError(f"replicates must be 1..{cfg.max_replicas}")

    step_cost = spec.steps * cfg.per_step_dct
    # Only *extra* replicas cost extra — the first one is the base job.
    replica_cost = max(0, replicates - 1) * cfg.per_replica_dct
    dct = cfg.base_dct + step_cost + replica_cost
    usdc = dct * cfg.dct_usd_rate
    tier = required_tier(usdc)

    return {
        "steps": spec.steps,
        "replicates": replicates,
        "work_units": spec.steps * replicates,
        "breakdownDct": {
            "base": cfg.base_dct,
            "steps": round(step_cost, 9),
            "replicas": round(replica_cost, 9),
        },
        "dct": round(dct, 9),
        "usdc": round(usdc, 6),
        "dct_usd_rate": cfg.dct_usd_rate,
        "tier": tier,
        # Be explicit: this tier is a *requirement*, and it is only honoured
        # if enough nodes exist to satisfy it (see run_job).
        "tier_enforceable": replicates >= (2 if tier == "REDUNDANT" else 1),
        "rate_note": (
            "Quote is Jambubrowser configuration, not an oracle; DCM's "
            "settlement receipts remain the source of truth."
        ),
    }


# Pricing

# ---------------------------------------------------------------------------
# Dispatch + verification
# ---------------------------------------------------------------------------

def execution_hash(spec_hash: str, node_id: str, output: Any) -> str:
    """Bind a result to the spec *and* the node that produced it.

    Any of the three changing produces a different hash, so a receipt can
    prove which artefact ran where.
    """
    return hashlib.sha256(
        js_dumps({"spec": spec_hash, "node": node_id, "output": output}).encode(
            "utf-8"
        )
    ).hexdigest()


async def _run_one(
    executor: SimulationExecutor, spec: dict, spec_hash: str, timeout_ms: int,
) -> dict:
    """Execute on one node and return a uniform attempt record."""
    started = time.monotonic()
    try:
        output = await asyncio.wait_for(
            executor.execute(spec), timeout=timeout_ms / 1000.0
        )
    except asyncio.TimeoutError:
        return {
            "node_id": executor.node_id, "ok": False,
            "error": f"timeout after {timeout_ms}ms",
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "output": None, "execution_hash": None,
        }
    except ExecutorError as e:
        return {
            "node_id": executor.node_id, "ok": False, "error": str(e),
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "output": None, "execution_hash": None,
        }
    except Exception as e:  # a node must never take the scheduler down
        log.warning("node %s raised: %s", executor.node_id, e)
        return {
            "node_id": executor.node_id, "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "output": None, "execution_hash": None,
        }
    return {
        "node_id": executor.node_id, "ok": True, "error": None,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "output": output,
        "execution_hash": execution_hash(spec_hash, executor.node_id, output),
    }


def verify_replicas(attempts: list[dict], cfg: SimulationConfig) -> dict:
    """Decide the verdict by **mesh consensus**, not by trusting node #1.

    Earlier behaviour treated the first responder as truth and compared every
    other node against it. That is wrong in the common real case: with three
    replicas where one drifts, the correct answer got quarantined *because*
    an honest node answered first.

    Instead each numeric path is compared against the **median** across
    replicas, and each replica is classified as agreeing or diverging. The
    verdict then depends on whether a strict majority exists:

    - all replicas agree → ``MATCH``, everyone settles
    - a strict majority agrees → ``MATCH_CONSENSUS``, the majority settles
      and the outliers are named and penalised (this is what makes paying
      for 3 replicas worth it)
    - the split is even, or no replica completes → ``MISMATCH``; nothing
      settles, because a tie carries no information about which side is
      right

    Honest limit: majority consensus assumes a minority of nodes are faulty
    (the usual ``n > 3f`` bound). It cannot detect a mesh that is *uniformly*
    wrong, and ``ATTESTED`` remains unimplemented — see
    ``docs/SIMULATION_COMPUTE.md``.
    """
    good = [a for a in attempts if a["ok"] and a["output"] is not None]
    if not good:
        return {
            "verdict": "MISMATCH", "reason": "no successful replica to verify",
            "replicas_compared": 0, "max_rel_deviation": None,
            "max_deviation_path": None, "comparisons": [],
            "agreeing": [], "diverging": [], "consensus": None,
            "majority": False,
        }
    if len(good) == 1:
        return {
            "verdict": "MATCH", "reason": None, "replicas_compared": 0,
            "max_rel_deviation": None, "max_deviation_path": None,
            "comparisons": [], "agreeing": [good[0]["node_id"]],
            "diverging": [], "consensus": good[0]["node_id"],
            "majority": True,
            "note": "single replica — nothing to cross-check against",
        }

    # Per-path median across replicas: robust to one node being wrong.
    medians = _path_medians([a["output"] for a in good])
    agreeing: list[str] = []
    diverging: list[str] = []
    comparisons: list[dict] = []

    for attempt in good:
        node_id = attempt["node_id"]
        # Compare path-to-path directly. Re-serialising the median map as a
        # JSON object would re-prefix every key ("$" -> "$.$"), so no path
        # would ever match and honest replicas would look divergent.
        parsed = parse_numeric(js_dumps(attempt["output"]))
        if not parsed["ok"]:
            agreeing_ok = False
            worst = 0.0
            worst_path = None
            missing: list[str] = []
            extra: list[str] = []
            reason = parsed["reason"]
        else:
            values = dict(parsed["numbers"])
            missing = [p for p in medians if p not in values]
            extra = [p for p in values if p not in medians]
            worst = 0.0
            worst_path = None
            for path in sorted(set(medians) & set(values)):
                delta = deviation(
                    values[path], medians[path], cfg.abs_tol, cfg.rel_tol
                )
                if delta > worst:
                    worst, worst_path = delta, path
            # A replica missing a path the consensus has is divergent even if
            # every number it does report matches.
            agreeing_ok = worst == 0.0 and not missing and not extra
            reason = None

        (agreeing if agreeing_ok else diverging).append(node_id)
        comparisons.append({
            "node": node_id,
            "verdict": "MATCH" if agreeing_ok else "MISMATCH",
            "classified": "AGREEING" if agreeing_ok else "DIVERGING",
            "agreement": round(1.0 - min(1.0, worst), 6),
            "max_rel_deviation": worst,
            "max_deviation_path": worst_path,
            "missing_vs_consensus": missing,
            "extra_vs_consensus": extra,
            "reason": reason,
        })

    total = len(good)
    majority = len(agreeing) * 2 > total
    disputed: list[str] = []
    if not majority:
        # An even split carries no information about which side is right, so
        # nobody is blamed. Marking both nodes "diverging" would be a lie that
        # also poisons reputation — with n=2 the median is the midpoint of two
        # different answers, so both sit equidistant from it by construction.
        disputed = sorted(agreeing + diverging)
        agreeing = []
        diverging = []

    worst_node = max(
        comparisons, key=lambda c: c["max_rel_deviation"] or 0.0
    )
    worst_dev = worst_node["max_rel_deviation"] or 0.0
    worst_path = worst_node["max_deviation_path"]

    return {
        "verdict": "MATCH" if not diverging and not disputed else (
            "MATCH_CONSENSUS" if majority else "MISMATCH"
        ),
        "reason": None if not (diverging or disputed) else (
            (f"{len(disputed)} replicas disputed with no majority — "
             "cannot attribute the disagreement to any node")
            if disputed else
            f"{len(diverging)} of {total} replicas diverged from consensus"
        ),
        "replicas_compared": len(comparisons),
        "max_rel_deviation": worst_dev or None,
        "max_deviation_path": worst_path,
        "comparisons": comparisons,
        "agreeing": sorted(agreeing),
        "diverging": sorted(diverging),
        "disputed": disputed,
        "consensus": agreeing[0] if agreeing else None,
        "majority": majority,
    }


def _path_medians(outputs: list) -> dict[str, float]:
    """Median value per JSON path across replica outputs.

    Uses the median rather than the mean so a single wildly wrong node cannot
    drag the reference value toward itself.
    """
    by_path: dict[str, list[float]] = {}
    for output in outputs:
        parsed = parse_numeric(js_dumps(output))
        if not parsed["ok"]:
            continue
        for path, value in parsed["numbers"]:
            by_path.setdefault(path, []).append(value)
    medians: dict[str, float] = {}
    for path, values in by_path.items():
        ordered = sorted(values)
        mid = len(ordered) // 2
        medians[path] = (
            ordered[mid] if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2.0
        )
    return medians


async def run_job(
    spec: SimulationSpec, *, replicates: int = 1, cfg: Optional[SimulationConfig] = None,
) -> dict:
    """Dispatch a frozen spec to the mesh, verify it, and settle or quarantine.

    Node selection is deterministic and every attempt is kept, so a result is
    reproducible and auditable. Selection order is: **unhealthy nodes excluded**
    (quarantined after repeated failures), then **reputation-ranked** (nodes
    whose numbers agreed with the mesh before), then node id as the tie-break.
    A node that fails is not silently skipped — the next candidate is tried and
    the failure stays in the attempt log.

    Settlement rule: the job settles when the required tier is satisfied and a
    majority (or all) replicas agreed. With a strict majority the diverging
    minority is named and penalised but does not block the job — that is what
    makes paying for a third replica worth it. With an even split, or too few
    nodes, nothing settles: ``charged_dct`` is 0, because a tie carries no
    information about which side is right.
    """
    cfg = cfg or SimulationConfig.from_env()
    default_registry()
    spec_hash = spec.spec_hash()
    canonical = spec.canonical()
    pricing = quote(spec, replicates=replicates)
    job_id = uuid.uuid4().hex

    # Read through the owning module: see the note on the import above.
    registry = executors.EXECUTORS
    serving = [
        registry[node_id]
        for node_id in sorted(registry)
        if registry[node_id].supports_kind(spec.kind)
    ]
    if not serving:
        return {
            "spec_hash": spec_hash, "spec": canonical, "quote": pricing,
            "status": STATUS_FAILED,
            "attempts": [], "verification": None,
            "error": (
                f"no registered node serves kind={spec.kind!r}; "
                f"available: {[n['node_id'] for n in available_nodes()]}"
            ),
            "charged_dct": 0.0, "nodes_used": [],
            "required_tier_satisfied": False,
            "excluded_nodes": {},
        }

    # Unhealthy nodes are skipped outright (quarantine expires on its own, so
    # a node that recovers rejoins without a restart).
    eligible = [e for e in serving if node_health(e.node_id).available]
    excluded = {
        e.node_id: node_health(e.node_id).to_dict()
        for e in serving if e not in eligible
    }

    # REDUNDANT means two *distinct* nodes; without them the job cannot meet
    # its own policy and says so rather than running single-node and
    # reporting a MATCH it did not earn.
    needs_redundancy = pricing["tier"] == "REDUNDANT"
    target = max(replicates, 2) if needs_redundancy else replicates
    if len(eligible) < target:
        reason = (
            f"tier {pricing['tier']} needs {target} available node(s), "
            f"only {len(eligible)} eligible of {len(serving)} registered"
        )
        if excluded:
            reason += f"; quarantined: {sorted(excluded)}"
        return {
            "spec_hash": spec_hash, "spec": canonical, "quote": pricing,
            "status": STATUS_QUARANTINED,
            "attempts": [], "verification": None,
            "error": reason,
            "charged_dct": 0.0, "nodes_used": [],
            "required_tier_satisfied": False,
            "excluded_nodes": excluded,
        }

    # Best-reputation nodes first, so a proven-diverging node is used only
    # when it is genuinely the only work capacity left.
    candidates = _candidate_order(eligible[:max(target, 1)], reputation())[:target]

    # Fan out concurrently; sequential failover would multiply latency by the
    # replica count for no benefit, since the candidates are independent.
    attempts = await asyncio.gather(
        *(
            _run_one(c, canonical, spec_hash, spec.timeout_ms)
            for c in candidates
        )
    )

    verification = verify_replicas(attempts, cfg)

    # Fold every attempt and every divergence into the durable health/reputation
    # state so the next job schedules around what we just learned.
    for attempt in attempts:
        _record_outcome(
            attempt["node_id"], bool(attempt["ok"]),
            attempt.get("error") or "", attempt["duration_ms"],
            cfg.failure_threshold,
        )
    for node_id in verification.get("diverging") or []:
        node_health(node_id).record_divergence()
    try:
        _publish_verdicts(verification, pricing["tier"], job_id)
    except Exception as e:  # scorecards are advisory; never fail a job for them
        log.warning("could not publish verification verdicts: %s", e)

    ok_attempts = [a for a in attempts if a["ok"]]
    # MATCH and MATCH_CONSENSUS both settle; only a tie (MISMATCH) blocks.
    settled_verdict = verification["verdict"] in ("MATCH", "MATCH_CONSENSUS")
    tier_satisfied = settled_verdict
    settled = tier_satisfied and len(ok_attempts) >= target

    if settled:
        status = STATUS_SETTLED
        error = None
    elif not ok_attempts:
        status = STATUS_FAILED
        error = "every node failed to execute the job"
    elif len(ok_attempts) < target:
        # Not a disagreement — the mesh simply could not field the work the
        # policy requires. Saying "did not agree" here would be wrong.
        status = STATUS_QUARANTINED
        failures = [a["error"] for a in attempts if not a["ok"]]
        error = (
            f"tier {pricing['tier']} needs {target} successful replicas, got "
            f"{len(ok_attempts)}; node errors: {failures}"
        )
    else:
        status = STATUS_QUARANTINED
        error = verification["reason"] or "replicas did not agree"

    return {
        "spec_hash": spec_hash,
        "spec": canonical,
        "quote": pricing,
        "status": status,
        "attempts": attempts,
        "verification": verification,
        "error": error,
        # Only verified work is charged.
        "charged_dct": pricing["dct"] if settled else 0.0,
        "nodes_used": [a["node_id"] for a in ok_attempts],
        "required_tier_satisfied": tier_satisfied,
        "excluded_nodes": excluded,
    }
