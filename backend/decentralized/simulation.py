"""
Decentralised simulation compute.

The mesh already *meters* simulation work — DCM emits ``simulation-charge``
receipts for it and MeshPay audits them — but nothing on this side could
submit, schedule, verify, or price a simulation. This module is that missing
middle: it turns a frozen, hash-pinned job spec into verified results, and
records what the work is owed in a form MeshPay can reconcile.

Design commitments:

- **The spec is frozen before dispatch.** ``spec_hash`` is computed from the
  canonical spec at submit time (the same ``js_dumps`` canonical form
  MeshPay and ``eval_cert`` use) and never recomputed from a result. Two
  nodes asked to run "the same" job can be *proved* to have been asked the
  same thing — which is the precondition for redundancy meaning anything.
- **``module_digest`` pins what code ran.** Supplying a sha256 of the
  Wasm/model bytes turns "this node returned the right numbers" into "this
  node returned the right numbers *from the artefact I meant to run*". A
  substituted binary changes the digest and is detectable.
- **Replicas must agree or nothing settles.** With ``replicates >= 2`` the
  outputs are compared using the ``numeric`` comparator (solver output is
  numbers — difflib over their string form is not verification). Divergence
  quarantines the job instead of paying for it.
- **A failure is never charged.** Settlement follows agreement; the charge
  recorded on the job is the amount *owed for verified work*, and an
  idempotency key makes a retried submit return the original job rather than
  double-charging it.

Honest limits: the local :class:`DeterministicExecutor` is a real, seedable
numeric kernel used for single-node operation and tests — it is not a
physics solver, and it must not be presented as one. Real workloads run
through :class:`DcmWasmExecutor` against a DCM node. There is no escrow and
no cross-epoch netting: ``charged_dct`` is an internal ledger figure that
MeshPay reconciles against DCM's own receipts, which remain the source of
truth.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.decentralized.meshpay import js_dumps
from backend.decentralized.verification import (
    DEFAULT_ABS_TOL,
    DEFAULT_REL_TOL,
    deviation,
    parse_numeric,
    record_verdict,
    required_tier,
    worker_report,
)

log = logging.getLogger("jambu.simulation")

#: Workload kinds a job can declare. ``wasm`` is the mesh's compiled-sandbox
#: lane; ``inference`` is a model on the node; ``native`` runs in-process.
JOB_KINDS = ("wasm", "inference", "native")

#: Job lifecycle. Dispatch is synchronous within the request, so a job is
#: written only once it has reached a terminal state — there are no
#: observable PENDING/RUNNING rows to poll. Queued/durable execution would
#: need a background worker, which does not exist yet; declaring those states
#: now would advertise a queue that is not there.
STATUS_SETTLED = "SETTLED"
STATUS_FAILED = "FAILED"
STATUS_QUARANTINED = "QUARANTINED"

TERMINAL_STATUSES = frozenset({STATUS_SETTLED, STATUS_FAILED, STATUS_QUARANTINED})

MAX_STEPS = 1_000_000
MAX_TIMEOUT_MS = 3_600_000
MAX_PARAMS_BYTES = 64 * 1024
_HEX = set("0123456789abcdef")


class SimulationError(ValueError):
    """Raised for a malformed job spec (maps to HTTP 422)."""


class ExecutorError(RuntimeError):
    """Raised by an executor when the node could not run the job."""


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "") or default)
    except ValueError:
        return default


@dataclass
class SimulationConfig:
    """Pricing + policy knobs (env-driven, single source for routes + CLI).

    Rates are *configuration*, not an oracle — the same honesty rule MeshPay
    applies to its DCT→USD rate. ``dct_usd_rate`` here only exists to feed
    the USD value into :func:`required_tier`; the money that actually moves
    comes from DCM's own receipts.
    """

    base_dct: float = 0.001           # per job, any size
    per_step_dct: float = 1e-5        # per simulation step
    per_replica_dct: float = 5e-5     # per extra replica (redundancy costs)
    dct_usd_rate: float = 0.01
    default_steps: int = 1_000
    default_timeout_ms: int = 30_000
    max_replicas: int = 5
    failure_threshold: int = 2      # consecutive node failures before quarantine
    abs_tol: float = DEFAULT_ABS_TOL
    rel_tol: float = DEFAULT_REL_TOL

    @classmethod
    def from_env(cls) -> "SimulationConfig":
        return cls(
            base_dct=_env_float("JAMBU_SIM_BASE_DCT", 0.001),
            per_step_dct=_env_float("JAMBU_SIM_PER_STEP_DCT", 1e-5),
            per_replica_dct=_env_float("JAMBU_SIM_REPLICATE_DCT", 5e-5),
            dct_usd_rate=_env_float("JAMBU_SIM_DCT_USD", 0.01),
            default_steps=_env_int("JAMBU_SIM_DEFAULT_STEPS", 1_000),
            default_timeout_ms=_env_int("JAMBU_SIM_TIMEOUT_MS", 30_000),
            max_replicas=_env_int("JAMBU_SIM_MAX_REPLICAS", 5),
            failure_threshold=_env_int("JAMBU_SIM_FAILURE_THRESHOLD", 2),
            abs_tol=_env_float("JAMBU_SIM_ABS_TOL", DEFAULT_ABS_TOL),
            rel_tol=_env_float("JAMBU_SIM_REL_TOL", DEFAULT_REL_TOL),
        )

    def describe(self) -> dict:
        return {
            "base_dct": self.base_dct,
            "per_step_dct": self.per_step_dct,
            "per_replica_dct": self.per_replica_dct,
            "dct_usd_rate": self.dct_usd_rate,
            "default_steps": self.default_steps,
            "default_timeout_ms": self.default_timeout_ms,
            "max_replicas": self.max_replicas,
            "failure_threshold": self.failure_threshold,
            "abs_tol": self.abs_tol,
            "rel_tol": self.rel_tol,
            "rate_note": (
                "Simulation rates and the DCT→USD rate are Jambubrowser "
                "configuration, not an oracle. DCM's receipts remain the "
                "settlement source of truth."
            ),
        }


@dataclass(frozen=True)
class SimulationSpec:
    """The frozen definition of one simulation job.

    Everything that can change the answer lives here and is covered by
    :attr:`spec_hash`. Anything that cannot (node id, wall-clock, which peer
    answered) is deliberately excluded so two nodes agreeing is meaningful.
    """

    kind: str
    module: str
    params: dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    steps: int = 0
    timeout_ms: int = 0
    module_digest: str = ""

    @classmethod
    def create(
        cls,
        *,
        kind: str = "wasm",
        module: str,
        params: Optional[dict] = None,
        seed: int = 0,
        steps: Optional[int] = None,
        timeout_ms: Optional[int] = None,
        module_digest: str = "",
    ) -> "SimulationSpec":
        """Validate and freeze a spec, filling defaults from config."""
        cfg = SimulationConfig.from_env()
        spec = cls(
            kind=(kind or "").strip(),
            module=(module or "").strip(),
            params=dict(params or {}),
            seed=int(seed or 0),
            steps=cfg.default_steps if steps is None else int(steps),
            timeout_ms=(
                cfg.default_timeout_ms if timeout_ms is None else int(timeout_ms)
            ),
            module_digest=(module_digest or "").strip().lower(),
        )
        spec.validate()
        return spec

    # -- validation -----------------------------------------------------

    def validate(self) -> None:
        if self.kind not in JOB_KINDS:
            raise SimulationError(
                f"kind must be one of {', '.join(JOB_KINDS)} (got {self.kind!r})"
            )
        if not self.module:
            raise SimulationError("module must not be empty")
        if len(self.module) > 200:
            raise SimulationError("module must be 200 characters or fewer")
        if not isinstance(self.params, dict):
            raise SimulationError("params must be an object")
        try:
            encoded = js_dumps(self.params)
        except (TypeError, ValueError) as e:
            raise SimulationError(f"params must be JSON-serialisable: {e}") from e
        if len(encoded.encode("utf-8")) > MAX_PARAMS_BYTES:
            raise SimulationError(
                f"params must be {MAX_PARAMS_BYTES} bytes or fewer"
            )
        if not 1 <= self.steps <= MAX_STEPS:
            raise SimulationError(f"steps must be 1..{MAX_STEPS}")
        if not 100 <= self.timeout_ms <= MAX_TIMEOUT_MS:
            raise SimulationError(f"timeout_ms must be 100..{MAX_TIMEOUT_MS}")
        if self.module_digest:
            if len(self.module_digest) != 64 or not set(
                self.module_digest
            ) <= _HEX:
                raise SimulationError(
                    "module_digest must be a 64-character sha256 hex digest"
                )

    # -- canonical form -------------------------------------------------

    def canonical(self) -> dict:
        """The exact object hashed into ``spec_hash`` (stable key order)."""
        return {
            "kind": self.kind,
            "module": self.module,
            "module_digest": self.module_digest,
            "params": self.params,
            "seed": self.seed,
            "steps": self.steps,
            "timeout_ms": self.timeout_ms,
        }

    def spec_hash(self) -> str:
        """sha256 over the canonical spec, frozen before any node sees it."""
        return hashlib.sha256(
            js_dumps(self.canonical()).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict:
        return {**self.canonical(), "spec_hash": self.spec_hash()}

    @classmethod
    def from_dict(cls, data: dict) -> "SimulationSpec":
        """Rebuild from a stored canonical object (``spec_hash`` ignored)."""
        return cls(
            kind=data.get("kind", ""),
            module=data.get("module", ""),
            params=dict(data.get("params") or {}),
            seed=int(data.get("seed") or 0),
            steps=int(data.get("steps") or 0),
            timeout_ms=int(data.get("timeout_ms") or 0),
            module_digest=data.get("module_digest", "") or "",
        )


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


# ---------------------------------------------------------------------------
# Executors (the compute nodes)
# ---------------------------------------------------------------------------

class SimulationExecutor:
    """A node that can run a spec: ``async execute(spec_dict) -> result``.

    ``spec_dict`` is the *canonical* spec (frozen, includes ``module_digest``)
    so a node can re-derive ``spec_hash`` and prove it ran what was asked.
    Subclasses must raise :class:`ExecutorError` on failure rather than
    returning a partial result — a partial result that looks successful is
    exactly the failure this ladder exists to catch.
    """

    node_id = "base"
    supports: tuple[str, ...] = JOB_KINDS

    def supports_kind(self, kind: str) -> bool:
        return kind in self.supports

    async def execute(self, spec: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class DeterministicExecutor(SimulationExecutor):
    """A real, seedable numeric kernel for single-node operation and tests.

    It is a genuine deterministic computation: the same ``(module, params,
    seed, steps)`` produces bit-identical numbers on every node, which is what
    makes redundancy meaningful without a live mesh. It is **not** a physics
    or CFD solver and must not be presented as one — it exists so the
    dispatch, replication, verification, and settlement paths are exercisable
    end-to-end, and so a single-node install has something honest to run.

    The kernel is a fixed-point iteration over a seeded state, which gives
    three properties the ladder cares about: it is deterministic, it has a
    natural "converged" signal, and it produces a numeric trajectory that
    differs visibly if a node's arithmetic drifts.
    """

    def __init__(self, node_id: str = "local-0"):
        self.node_id = node_id

    async def execute(self, spec: dict) -> dict:
        import random

        params = spec.get("params") or {}
        steps = int(spec.get("steps") or 0)
        seed = int(spec.get("seed") or 0)

        # The module name only selects the damping profile, so different
        # modules produce different-but-still-deterministic trajectories.
        damping = {
            "heat": 0.98, "fluid": 0.95, "solve": 0.9, "default": 0.99,
        }.get(str(spec.get("module", "")), 0.97)
        amplitude = float(params.get("amplitude") or 1.0)
        trace_every = max(1, int(params.get("trace_every") or max(1, steps // 8)))

        rng = random.Random(f"{seed}:{spec.get('module')}:{amplitude}")
        state = amplitude
        total = 0.0
        trace: list[float] = []
        for index in range(steps):
            state = state * damping + rng.uniform(-0.01, 0.01)
            total += state
            if index % trace_every == 0:
                trace.append(round(state, 12))

        mean = total / steps if steps else 0.0
        return {
            "module": spec.get("module"),
            "steps": steps,
            "seed": seed,
            "final_state": round(state, 12),
            "mean_state": round(mean, 12),
            "converged": abs(state) < max(1.0, amplitude),
            "trace": trace,
        }


class DcmWasmExecutor(SimulationExecutor):
    """Runs the spec on a real DecentraCode Mesh node.

    Uses the node's Wasm/simulation endpoint. The spec hash is echoed back so
    the scheduler can prove the node answered for *this* job and not a
    replayed one; a mismatch is a hard failure rather than a warning.
    """

    supports = ("wasm",)

    def __init__(self, node_id: str, client: Any, *, path: str = "/api/simulate"):
        self.node_id = node_id
        self._client = client
        self.path = path

    async def execute(self, spec: dict) -> dict:
        from backend.decentralized.dcm_client import DcmError

        payload = {"spec": spec, "specHash": _spec_hash_of(spec)}
        try:
            response = await self._client.post(self.path, json=payload)
        except DcmError as e:
            raise ExecutorError(f"DCM {e.status_code}: {e.detail}") from e
        except Exception as e:  # transport-level failure
            raise ExecutorError(f"DCM transport error: {e}") from e

        echoed = response.get("specHash") or response.get("spec_hash")
        if echoed and echoed != payload["specHash"]:
            raise ExecutorError(
                f"node echoed specHash {str(echoed)[:16]}… but this job is "
                f"{payload['specHash'][:16]}… — refusing a stale answer"
            )
        if "output" not in response and "result" not in response:
            raise ExecutorError("DCM returned no output/result field")
        return response.get("output", response.get("result"))


class FaultInjectingExecutor(DeterministicExecutor):
    """Test double that drifts the output off the true value.

    Models the silent-divergence failure redundancy exists to catch: the
    node reports success, but its numbers are wrong by a factor or a field is
    missing — differences difflib would happily score as "similar".
    """

    def __init__(self, node_id: str, *, scale: float = 1.001, drop_field: str = ""):
        super().__init__(node_id)
        self.scale = scale
        self.drop_field = drop_field

    async def execute(self, spec: dict) -> dict:
        out = await super().execute(spec)
        out = {
            key: (
                [round(v * self.scale, 12) for v in value]
                if isinstance(value, list)
                else round(value * self.scale, 12)
                if isinstance(value, float)
                else value
            )
            for key, value in out.items()
        }
        if self.drop_field:
            out.pop(self.drop_field, None)
        return out


def _spec_hash_of(spec: dict) -> str:
    """Recompute ``spec_hash`` from a canonical spec dict."""
    return hashlib.sha256(js_dumps(spec).encode("utf-8")).hexdigest()


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


# ---------------------------------------------------------------------------
# Node registry
# ---------------------------------------------------------------------------

EXECUTORS: dict[str, SimulationExecutor] = {}


def register_executor(executor: SimulationExecutor) -> None:
    EXECUTORS[executor.node_id] = executor


def unregister_executor(node_id: str) -> bool:
    return EXECUTORS.pop(node_id, None) is not None


def available_nodes(kind: Optional[str] = None) -> list[dict]:
    """Registered nodes, optionally filtered to those serving ``kind``."""
    nodes = [
        {"node_id": e.node_id, "supports": list(e.supports)}
        for e in EXECUTORS.values()
        if kind is None or e.supports_kind(kind)
    ]
    return sorted(nodes, key=lambda n: n["node_id"])


def default_registry() -> None:
    """Seed one in-process node when nothing else is registered.

    Keeps a single-node install able to run and *verify* jobs (a job can be
    replicated only if more than one node exists, and the registry says so
    plainly rather than pretending a replica happened).
    """
    if not EXECUTORS:
        register_executor(DeterministicExecutor("local-0"))


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

    serving = [
        EXECUTORS[node_id]
        for node_id in sorted(EXECUTORS)
        if EXECUTORS[node_id].supports_kind(spec.kind)
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


# ---------------------------------------------------------------------------
# Job store (idempotency + history)
# ---------------------------------------------------------------------------

def save_job(
    result: dict, *, job_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """Persist a run result and return the stored record.

    When ``idempotency_key`` matches an existing job the stored record is
    returned unchanged — that is the point: a retried submit must not bill a
    second time for work already dispatched.
    """
    from backend.core.database import get_db

    if idempotency_key:
        existing = get_job_by_idempotency_key(idempotency_key)
        if existing:
            return existing

    record = {
        "id": job_id or uuid.uuid4().hex,
        "idempotency_key": idempotency_key,
        "spec_hash": result["spec_hash"],
        "spec_json": js_dumps(result["spec"]),
        "status": result["status"],
        "tier": result["quote"]["tier"],
        "replicas": result["quote"]["replicates"],
        "quote_json": js_dumps(result["quote"]),
        "result_json": js_dumps(result),
        "charged_dct": float(result.get("charged_dct") or 0),
        "error": result.get("error"),
    }
    now = time.time()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO simulation_jobs
                (id, idempotency_key, spec_hash, spec_json, status, tier,
                 replicas, quote_json, result_json, charged_dct, error,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"], record["idempotency_key"], record["spec_hash"],
                record["spec_json"], record["status"], record["tier"],
                record["replicas"], record["quote_json"],
                record["result_json"], record["charged_dct"], record["error"],
                now, now,
            ),
        )
        conn.commit()
    # Return the same shape a later read produces, so a submit response and a
    # subsequent GET are directly comparable.
    return {
        **record,
        "spec": result["spec"],
        "quote": result["quote"],
        "result": result,
        "created_at": now,
        "updated_at": now,
    }


def _row_to_job(row) -> dict:
    record = dict(row)
    for blob, key in (("spec_json", "spec"), ("quote_json", "quote"),
                      ("result_json", "result")):
        try:
            record[key] = json.loads(record.pop(blob) or "{}")
        except ValueError:
            record[key] = {}
    return record


def get_job(job_id: str) -> Optional[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM simulation_jobs WHERE id = ?", (job_id,),
        ).fetchone()
    return _public_record(_row_to_job(row)) if row else None


def get_job_by_idempotency_key(key: str) -> Optional[dict]:
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM simulation_jobs WHERE idempotency_key = ?", (key,),
        ).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    limit: int = 50, *, status: Optional[str] = None, spec_hash: Optional[str] = None,
) -> list[dict]:
    from backend.core.database import get_db

    query = "SELECT * FROM simulation_jobs"
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if spec_hash:
        clauses.append("spec_hash = ?")
        params.append(spec_hash)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_job(r) for r in rows]


def job_totals() -> dict:
    """Aggregate spend and outcome counts — the operator's headline numbers."""
    from backend.core.database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n, SUM(charged_dct) AS charged "
            "FROM simulation_jobs GROUP BY status"
        ).fetchall()
    by_status = {
        r["status"]: {"jobs": r["n"], "chargedDct": round(r["charged"] or 0, 9)}
        for r in rows
    }
    return {
        "by_status": by_status,
        "jobs": sum(v["jobs"] for v in by_status.values()),
        # Quarantined work is the number worth watching: it is work that was
        # dispatched, not paid, and usually means a node is misbehaving.
        "chargedDct": round(
            sum(v["chargedDct"] for v in by_status.values()), 9
        ),
        "unpaidJobs": by_status.get(STATUS_QUARANTINED, {}).get("jobs", 0)
        + by_status.get(STATUS_FAILED, {}).get("jobs", 0),
    }


# ---------------------------------------------------------------------------
# Submit (the route/CLI/MCP entry point)
# ---------------------------------------------------------------------------

def _public_record(stored: dict) -> dict:
    """One flat, self-describing job record for every surface.

    The store keeps ``result_json`` (the full run record) and the audit keeps
    scalar columns, but callers — routes, CLI, MCP — should not have to know
    which is which, so both shapes are merged here into a single payload.
    """
    result = stored.get("result") or {}
    quote_block = stored.get("quote") or result.get("quote") or {}
    verification = result.get("verification")
    return {
        "id": stored.get("id"),
        "idempotency_key": stored.get("idempotency_key"),
        "spec_hash": stored.get("spec_hash"),
        "spec": stored.get("spec") or result.get("spec"),
        "quote": quote_block,
        "tier": stored.get("tier") or quote_block.get("tier"),
        "replicas": stored.get("replicas", quote_block.get("replicates", 1)),
        "status": stored.get("status") or result.get("status"),
        "attempts": result.get("attempts", []),
        "verification": verification,
        "nodes_used": result.get("nodes_used", []),
        "required_tier_satisfied": result.get("required_tier_satisfied"),
        "charged_dct": stored.get("charged_dct", 0),
        "error": stored.get("error") or result.get("error"),
        "created_at": stored.get("created_at"),
        "updated_at": stored.get("updated_at"),
    }


async def submit(
    spec: SimulationSpec,
    *,
    replicates: int = 1,
    idempotency_key: Optional[str] = None,
    persist: bool = True,
) -> dict:
    """Freeze → quote → dispatch → verify → settle, then persist.

    ``idempotency_key`` short-circuits before dispatch: a retry returns the
    stored job and spends nothing, so a client that times out mid-flight and
    retries cannot be billed twice for the same work.
    """
    if idempotency_key and persist:
        existing = get_job_by_idempotency_key(idempotency_key)
        if existing:
            return {**_public_record(existing), "idempotent_replay": True}

    # Validate the replica count before any work is dispatched, so a bad
    # request never reaches the mesh.
    quote(spec, replicates=replicates)
    result = await run_job(spec, replicates=replicates)
    if not persist:
        return {**result, "id": uuid.uuid4().hex, "idempotent_replay": False}
    stored = save_job(result, idempotency_key=idempotency_key)
    return {**_public_record(stored), "idempotent_replay": False}


# ---------------------------------------------------------------------------
# Durable queue: enqueue now, settle later, survive a restart
# ---------------------------------------------------------------------------

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
OPEN_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})


def _insert_job(record: dict, *, idempotency_key: Optional[str]) -> dict:
    from backend.core.database import get_db

    now = time.time()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO simulation_jobs
                (id, idempotency_key, spec_hash, spec_json, status, tier,
                 replicas, quote_json, result_json, charged_dct, error,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"], idempotency_key, record["spec_hash"],
                js_dumps(record["spec"]), record["status"], record["tier"],
                record["replicas"], js_dumps(record["quote"]),
                js_dumps(record.get("result") or {}),
                record.get("charged_dct", 0.0), record.get("error"),
                now, now,
            ),
        )
        conn.commit()
    return {**record, "created_at": now, "updated_at": now}


def enqueue_job(
    spec: SimulationSpec,
    *,
    replicates: int = 1,
    idempotency_key: Optional[str] = None,
) -> dict:
    """Persist a QUEUED job without dispatching it.

    The spec is frozen and validated before anything is written, so a bad
    request never enters the queue. ``idempotency_key`` deduplicates before
    insert, exactly like the synchronous path.
    """
    if idempotency_key:
        existing = get_job_by_idempotency_key(idempotency_key)
        if existing:
            return {**_public_record(existing), "idempotent_replay": True}
    pricing = quote(spec, replicates=replicates)  # validates too
    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "idempotency_key": idempotency_key,
        "spec_hash": spec.spec_hash(),
        "spec": spec.canonical(),
        "status": STATUS_QUEUED,
        "tier": pricing["tier"],
        "replicas": replicates,
        "quote": pricing,
        "charged_dct": 0.0,
        "error": None,
        "result": {},
    }
    stored = _insert_job(record, idempotency_key=idempotency_key)
    return {**_public_record(stored), "idempotent_replay": False}


def _claim_next_queued() -> Optional[dict]:
    """Atomically flip the oldest QUEUED job to RUNNING and return it."""
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM simulation_jobs WHERE status = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (STATUS_QUEUED,),
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE simulation_jobs SET status = ?, updated_at = ? "
            "WHERE id = ? AND status = ?",
            (STATUS_RUNNING, time.time(), row["id"], STATUS_QUEUED),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None  # another worker claimed it first
        return _row_to_job(row)


def _finish_job(job_id: str, result: dict) -> None:
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute(
            "UPDATE simulation_jobs SET status = ?, result_json = ?, "
            "charged_dct = ?, error = ?, tier = ?, updated_at = ? WHERE id = ?",
            (
                result["status"],
                js_dumps(result),
                float(result.get("charged_dct") or 0),
                result.get("error"),
                (result.get("quote") or {}).get("tier"),
                time.time(),
                job_id,
            ),
        )
        conn.commit()


def recover_interrupted_jobs() -> int:
    """Re-queue jobs a dead process left in RUNNING. Returns the count."""
    from backend.core.database import get_db

    with get_db() as conn:
        cur = conn.execute(
            "UPDATE simulation_jobs SET status = ?, updated_at = ? WHERE status = ?",
            (STATUS_QUEUED, time.time(), STATUS_RUNNING),
        )
        conn.commit()
        return cur.rowcount


class SimulationWorker:
    """FIFO worker that drains the durable queue inside the engine process.

    One job at a time by design: a node that is mid-job is known-busy, and
    the honesty constraint says we must not pretend to field more capacity
    than we actually have. Start it with the engine lifespan; without it the
    synchronous path is the only way jobs run.
    """

    def __init__(self, *, poll: float = 2.0):
        self._poll = max(0.1, float(poll))
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        recover_interrupted_jobs()
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_one(self) -> bool:
        row = _claim_next_queued()
        if row is None:
            return False
        try:
            spec = SimulationSpec.from_dict(row["spec"])
            result = await run_job(spec, replicates=row.get("replicas") or 1)
        except Exception as exc:  # noqa: BLE001 - surface, never kill the loop
            log.warning("queued simulation job %s failed: %s", row["id"], exc)
            result = {
                "spec_hash": row["spec_hash"],
                "spec": row.get("spec") or {},
                "quote": row.get("quote") or {},
                "status": STATUS_FAILED,
                "attempts": [],
                "verification": None,
                "error": str(exc),
                "charged_dct": 0.0,
                "nodes_used": [],
                "required_tier_satisfied": False,
                "excluded_nodes": {},
            }
        _finish_job(row["id"], result)
        return True

    async def _loop(self) -> None:
        while True:
            try:
                did = await self.run_one()
            except Exception as exc:  # noqa: BLE001 - the loop must not die
                log.warning("simulation worker loop error: %s", exc)
                did = False
            if not did:
                await asyncio.sleep(self._poll)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def simulation_bundle(job_id: str) -> dict:
    """Sign one job into an Ed25519 evidence bundle (kind compute_simulation).

    The bundle carries the frozen spec, the spec hash, every node's
    execution hash, and the numeric verification report — so a third party
    can re-derive what was asked, who answered, and whether they agreed,
    without trusting this process. Verify with
    ``scripts/verify_evidence_bundle.py``.
    """
    from backend.decentralized.evidence import build_bundle

    # get_job returns the flattened public record, so read attempts from it.
    job = get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    verification = job.get("verification") or {}
    payload = {
        "job_id": job["id"],
        "spec_hash": job["spec_hash"],
        "spec": job.get("spec"),
        "module_digest": (job.get("spec") or {}).get("module_digest", ""),
        "tier": job.get("tier"),
        "replicas": job.get("replicas"),
        "status": job["status"],
        "charged_dct": job.get("charged_dct", 0),
        "quote": job.get("quote"),
        "verification": verification or None,
        "nodes_used": job.get("nodes_used", []),
        "execution_hashes": {
            a["node_id"]: a.get("execution_hash")
            for a in job.get("attempts") or []
        },
        "error": job.get("error"),
    }
    # Egress provenance: the network environment this job was verified in.
    try:
        from backend.core.vpn import get_vpn_manager

        manager = get_vpn_manager()
        payload["egress"] = {
            "enabled": manager.enabled,
            "tunnel_kind": manager.config.tunnel_kind.value,
            "pq_requested": manager.config.pq or "",
            "pool_endpoints": manager.pool.size,
        }
    except Exception:  # never break evidence for VPN plumbing
        payload["egress"] = {"enabled": False}
    return build_bundle(
        "compute_simulation",
        {
            "job_id": job["id"],
            "status": job["status"],
            "verdict": verification.get("verdict"),
        },
        payload,
    )