"""Node health (can this node run right now) and reputation (does it agree
with the rest of the mesh).

The two are deliberately separate. Health quarantines a node that keeps
failing, which is the same policy the VPN proxy pool uses, so both
subsystems self-heal on the same terms. Reputation only *ranks*: a
low-reputation node still runs, it just runs last. Confusing the two is
how you end up excluding every node because the mesh disagrees.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from backend.decentralized.verification import record_verdict, worker_report

# One logger name for the whole subsystem, so a log filter on
# "jambu.simulation" still catches every module after the split.
log = logging.getLogger("jambu.simulation")

# ---------------------------------------------------------------------------
# Node health (quarantine + self-healing)
# ---------------------------------------------------------------------------
#
# Mirrors the VPN pool's EndpointHealth so both subsystems behave the same
# way: a node that repeatedly fails is skipped for a bounded window and then
# gets another chance, so a transient blip self-heals without a restart.
#
# Health is distinct from *reputation*. Health is "can this node run a job
# right now" (failures/timeouts/quarantine). Reputation is "does it agree
# with the rest of the mesh" (see :func:`reputation`).

@dataclass
class NodeHealth:
    """Rolling health record for one compute node."""

    node_id: str
    healthy: bool = True
    consecutive_failures: int = 0
    successes: int = 0
    failures: int = 0
    quarantined_until: float = 0.0
    last_checked: float = 0.0
    last_error: str = ""
    latency_ms: Optional[float] = None
    #: Jobs where this node's numbers diverged from the mesh consensus.
    divergences: int = 0

    @property
    def available(self) -> bool:
        """Usable right now.

        Deliberately based on the quarantine *window* only, not on ``healthy``.
        A node that fails is marked unhealthy and skipped for a bounded period;
        when that period expires the node is offered work again, and only a
        successful run clears ``healthy``. Keying availability off ``healthy``
        instead would be a liveness bug: an excluded node never runs, so it
        never accumulates a success, so it stays dead forever and the
        quarantine could never self-heal.
        """
        return time.time() >= self.quarantined_until

    @property
    def quarantined(self) -> bool:
        return self.quarantined_until > time.time()

    def record_success(self, latency_ms: Optional[float] = None) -> None:
        self.successes += 1
        self.consecutive_failures = 0
        self.healthy = True
        self.quarantined_until = 0.0
        self.last_checked = time.time()
        self.last_error = ""
        if latency_ms is not None:
            self.latency_ms = (
                latency_ms if self.latency_ms is None
                else self.latency_ms * 0.7 + latency_ms * 0.3  # EWMA
            )

    def record_failure(self, error: str, threshold: int) -> None:
        self.failures += 1
        self.consecutive_failures += 1
        self.last_checked = time.time()
        self.last_error = (error or "")[:200]
        if threshold > 0 and self.consecutive_failures >= threshold:
            self.healthy = False
            # Short bounded backoff so a transient blip self-heals.
            self.quarantined_until = time.time() + min(
                300.0, 5.0 * self.consecutive_failures
            )

    def record_divergence(self) -> None:
        self.divergences += 1

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "healthy": self.healthy,
            "available": self.available,
            "quarantined": self.quarantined,
            "quarantined_until": self.quarantined_until or None,
            "consecutive_failures": self.consecutive_failures,
            "successes": self.successes,
            "failures": self.failures,
            "divergences": self.divergences,
            "latency_ms": round(self.latency_ms, 1)
            if self.latency_ms is not None else None,
            "last_checked": self.last_checked or None,
            "last_error": self.last_error,
        }


NODE_HEALTH: dict[str, NodeHealth] = {}


def node_health(node_id: str) -> NodeHealth:
    """Get (or lazily create) the health record for a node."""
    return NODE_HEALTH.setdefault(node_id, NodeHealth(node_id))


def reset_node_health() -> None:
    NODE_HEALTH.clear()


def _record_outcome(node_id: str, ok: bool, error: str, latency_ms: float,
                    threshold: int) -> None:
    health = node_health(node_id)
    if ok:
        health.record_success(latency_ms)
    else:
        health.record_failure(error, threshold)


# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------
#
# Health says "can this node run right now". Reputation says "does it agree
# with the rest of the mesh" and is derived from the scorecards that already
# exist in ``verification`` — so the existing ``GET /verification/workers``
# surface and its per-worker agreement rate become the scheduling signal too,
# rather than a report nobody acts on.

#: Agreement rate below which a node is reported as untrusted (not excluded:
#: reputation ranks, health excludes).
REPUTATION_FLOOR = 0.5


def reputation() -> dict[str, dict]:
    """Per-node reliability from the verification scorecards.

    Returns ``{node_id: {agreement_rate, canary_rate, mismatches, errors,
    jobs, trusted, score}}``. Nodes with no recorded history get
    ``score = None`` so callers can treat "unknown" differently from "bad" —
    a brand-new node must not be ranked below a proven-bad one.
    """
    report = worker_report()
    out: dict[str, dict] = {}
    for worker in report.get("workers", []):
        node_id = worker["worker_id"]
        agreement = worker.get("redundancy_agreement_rate")
        canary = worker.get("canary_pass_rate")
        mismatches = worker.get("mismatches", 0)
        errors = worker.get("errors", 0)
        runs = worker.get("redundancy_runs", 0) or 0

        # Only nodes with evidence get a score; absence of evidence is not
        # evidence of badness.
        score: Optional[float] = None
        if runs > 0 and agreement is not None:
            score = float(agreement)
            if canary is not None:
                score = score * 0.8 + float(canary) * 0.2
            # Errored runs (not mismatches) are penalised harder: a node that
            # did not produce an answer is worse than one that disagreed.
            score -= min(0.3, errors * 0.05)
            score = max(0.0, min(1.0, score))

        out[node_id] = {
            "agreement_rate": agreement,
            "canary_rate": canary,
            "redundancy_runs": runs,
            "mismatches": mismatches,
            "errors": errors,
            "divergences": node_health(node_id).divergences,
            "score": round(score, 4) if score is not None else None,
            "trusted": score is None or score >= REPUTATION_FLOOR,
        }
    return out


def _publish_verdicts(verification: dict, tier: str, job_id: str) -> None:
    """Feed this job's outcome into the shared verification scorecards.

    Without this the ``worker_verdicts`` table only ever sees
    ``/verification/redundant`` traffic, so a mesh running simulation jobs
    would show empty worker reports and the reputation signal above would be
    permanently blank.
    """
    for comparison in verification.get("comparisons") or []:
        node_id = comparison["node"]
        if node_id in (verification.get("disputed") or []):
            # Disputed (no majority): we genuinely do not know whose answer is
            # wrong, so no verdict is published and no node is penalised.
            continue
        record_verdict(
            kind="redundant",
            worker_id=node_id,
            verdict="MATCH" if comparison["classified"] == "AGREEING" else "MISMATCH",
            tier=tier,
            detail={
                "job_id": job_id,
                "source": "simulation",
                "deviation": comparison["max_rel_deviation"],
                "path": comparison["max_deviation_path"],
            },
        )


def _candidate_order(candidates: list, scores: dict[str, dict]) -> list:
    """Order candidates best-first: healthy reputation, then node id.

    Unscored (fresh) nodes sort after proven-good ones but *before* proven-bad
    ones. Sorting is total and deterministic, so a job's node selection is
    reproducible for a given health state.
    """
    def key(executor):
        entry = scores.get(executor.node_id)
        score = entry.get("score") if entry else None
        # None -> 0.5 neutral: below a proven-good node, above a proven-bad one.
        rank = 0.5 if score is None else float(score)
        return (-rank, executor.node_id)

    return sorted(candidates, key=key)
