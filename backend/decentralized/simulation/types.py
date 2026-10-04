"""Job vocabulary: kinds, statuses, limits, errors, and the two dataclasses
every other module takes as input.

This is the leaf of the simulation package. `SimulationSpec` is the validation
boundary — it turns untrusted request JSON into something an executor can be
handed — and `SimulationConfig` is the single place the pricing and policy knobs
are read from the environment, so routes, CLI and MCP cannot disagree about a
price.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.decentralized.meshpay import js_dumps
from backend.decentralized.verification import DEFAULT_ABS_TOL, DEFAULT_REL_TOL

# One logger name for the whole subsystem, so a log filter on
# "jambu.simulation" still catches every module after the split.
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
