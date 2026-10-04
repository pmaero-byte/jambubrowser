"""Decentralised simulation compute: quote, dispatch, verify, settle.

This was one 1,542-line module. It is now five, layered so the import graph
stays acyclic and each piece of shared state lives with its only mutators:

    types.py     kinds, statuses, limits, errors, SimulationConfig/Spec  (leaf)
    health.py    NODE_HEALTH (quarantine) + reputation (ranking)
    executors.py EXECUTORS + the four executors that run a spec
    runner.py    quote, replica verification, dispatch (run_job)
    jobs.py      job store, idempotency, submit, durable queue, evidence

The whole previous public surface is re-exported here, so callers are
unaffected: `from backend.decentralized import simulation` and
`simulation.run_job(...)` mean exactly what they did.

`EXECUTORS` and `NODE_HEALTH` are the one thing that cannot be a plain
re-export: they are mutable dicts owned by `executors` and `health`, and a
`from ... import` in this file would bind one snapshot of each at import time.
They are resolved dynamically below, so `simulation.EXECUTORS` keeps meaning
"the live registry" — reading it, and rebinding it, behave as they did when
everything lived in one module.
"""
from __future__ import annotations

from backend.decentralized.simulation.types import Any
from backend.decentralized.simulation.types import DEFAULT_ABS_TOL
from backend.decentralized.simulation.types import DEFAULT_REL_TOL
from backend.decentralized.simulation.executors import DcmWasmExecutor
from backend.decentralized.simulation.executors import DeterministicExecutor
from backend.decentralized.simulation.types import ExecutorError
from backend.decentralized.simulation.executors import FaultInjectingExecutor
from backend.decentralized.simulation.types import JOB_KINDS
from backend.decentralized.simulation.types import MAX_PARAMS_BYTES
from backend.decentralized.simulation.types import MAX_STEPS
from backend.decentralized.simulation.types import MAX_TIMEOUT_MS
from backend.decentralized.simulation.health import NodeHealth
from backend.decentralized.simulation.jobs import OPEN_STATUSES
from backend.decentralized.simulation.types import Optional
from backend.decentralized.simulation.health import REPUTATION_FLOOR
from backend.decentralized.simulation.types import STATUS_FAILED
from backend.decentralized.simulation.types import STATUS_QUARANTINED
from backend.decentralized.simulation.jobs import STATUS_QUEUED
from backend.decentralized.simulation.jobs import STATUS_RUNNING
from backend.decentralized.simulation.types import STATUS_SETTLED
from backend.decentralized.simulation.types import SimulationConfig
from backend.decentralized.simulation.types import SimulationError
from backend.decentralized.simulation.executors import SimulationExecutor
from backend.decentralized.simulation.types import SimulationSpec
from backend.decentralized.simulation.jobs import SimulationWorker
from backend.decentralized.simulation.types import TERMINAL_STATUSES
from backend.decentralized.simulation.types import _HEX
from backend.decentralized.simulation.health import _candidate_order
from backend.decentralized.simulation.jobs import _claim_next_queued
from backend.decentralized.simulation.types import _env_float
from backend.decentralized.simulation.types import _env_int
from backend.decentralized.simulation.jobs import _finish_job
from backend.decentralized.simulation.jobs import _insert_job
from backend.decentralized.simulation.runner import _path_medians
from backend.decentralized.simulation.jobs import _public_record
from backend.decentralized.simulation.health import _publish_verdicts
from backend.decentralized.simulation.health import _record_outcome
from backend.decentralized.simulation.jobs import _row_to_job
from backend.decentralized.simulation.runner import _run_one
from backend.decentralized.simulation.executors import _spec_hash_of
import asyncio
from backend.decentralized.simulation.executors import available_nodes
from backend.decentralized.simulation.types import dataclass
from backend.decentralized.simulation.executors import default_registry
from backend.decentralized.simulation.runner import deviation
from backend.decentralized.simulation.jobs import enqueue_job
from backend.decentralized.simulation.runner import execution_hash
from backend.decentralized.simulation.types import field
from backend.decentralized.simulation.jobs import get_job
from backend.decentralized.simulation.jobs import get_job_by_idempotency_key
import hashlib
from backend.decentralized.simulation.jobs import job_totals
from backend.decentralized.simulation.types import js_dumps
import json
from backend.decentralized.simulation.jobs import list_jobs
import logging
from backend.decentralized.simulation.health import node_health
import os
from backend.decentralized.simulation.runner import parse_numeric
from backend.decentralized.simulation.jobs import quote
from backend.decentralized.simulation.health import record_verdict
from backend.decentralized.simulation.jobs import recover_interrupted_jobs
from backend.decentralized.simulation.executors import register_executor
from backend.decentralized.simulation.health import reputation
from backend.decentralized.verification import required_tier
from backend.decentralized.simulation.health import reset_node_health
from backend.decentralized.simulation.runner import run_job
from backend.decentralized.simulation.jobs import save_job
from backend.decentralized.simulation.jobs import simulation_bundle
from backend.decentralized.simulation.jobs import submit
import time
from backend.decentralized.simulation.executors import unregister_executor
import uuid
from backend.decentralized.simulation.runner import verify_replicas
from backend.decentralized.simulation.health import worker_report


# The old module exposed a module-level logger; keep it so anything that
# configured "jambu.simulation" still finds one here. Every submodule uses this
# same logger name.
log = logging.getLogger("jambu.simulation")

#: The mutable registries, mapped to the module that owns them.
_LIVE_STATE = {
    "EXECUTORS": "executors",
    "NODE_HEALTH": "health",
}


def __getattr__(name: str):
    """Forward the live registries to the module that owns each one.

    Needed because a static `from ... import EXECUTORS` would freeze the dict
    object at import time: rebinding `executors.EXECUTORS` (test isolation, or
    an embedder injecting its own nodes) would leave this name stale.
    """
    module_name = _LIVE_STATE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f"backend.decentralized.simulation.{module_name}"),
                   name)


def __dir__():
    return sorted(set(globals()) | set(_LIVE_STATE))

__all__ = [    "Any",
    "DEFAULT_ABS_TOL",
    "DEFAULT_REL_TOL",
    "DcmWasmExecutor",
    "DeterministicExecutor",
    "EXECUTORS",
    "ExecutorError",
    "FaultInjectingExecutor",
    "JOB_KINDS",
    "MAX_PARAMS_BYTES",
    "MAX_STEPS",
    "MAX_TIMEOUT_MS",
    "NODE_HEALTH",
    "NodeHealth",
    "OPEN_STATUSES",
    "Optional",
    "REPUTATION_FLOOR",
    "STATUS_FAILED",
    "STATUS_QUARANTINED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SETTLED",
    "SimulationConfig",
    "SimulationError",
    "SimulationExecutor",
    "SimulationSpec",
    "SimulationWorker",
    "TERMINAL_STATUSES",
    "_HEX",
    "_candidate_order",
    "_claim_next_queued",
    "_env_float",
    "_env_int",
    "_finish_job",
    "_insert_job",
    "_path_medians",
    "_public_record",
    "_publish_verdicts",
    "_record_outcome",
    "_row_to_job",
    "_run_one",
    "_spec_hash_of",
    "asyncio",
    "available_nodes",
    "dataclass",
    "default_registry",
    "deviation",
    "enqueue_job",
    "execution_hash",
    "field",
    "get_job",
    "get_job_by_idempotency_key",
    "hashlib",
    "job_totals",
    "js_dumps",
    "json",
    "list_jobs",
    "logging",
    "node_health",
    "os",
    "parse_numeric",
    "quote",
    "record_verdict",
    "recover_interrupted_jobs",
    "register_executor",
    "reputation",
    "required_tier",
    "reset_node_health",
    "run_job",
    "save_job",
    "simulation_bundle",
    "submit",
    "time",
    "unregister_executor",
    "uuid",
    "verify_replicas",
    "worker_report",
]
