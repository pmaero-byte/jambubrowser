"""The executors (a node that can run a spec) and the registry of them.

`EXECUTORS` lives here because every function that reads or writes it
lives here. It is the one piece of shared mutable state the package
exposes, and keeping it with its only mutators is what makes the split
safe.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from backend.decentralized.meshpay import js_dumps
from backend.decentralized.simulation.types import JOB_KINDS, ExecutorError

# One logger name for the whole subsystem, so a log filter on
# "jambu.simulation" still catches every module after the split.
log = logging.getLogger("jambu.simulation")

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
