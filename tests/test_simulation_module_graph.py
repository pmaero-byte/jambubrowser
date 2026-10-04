"""The simulation package must have an acyclic, import-order-independent graph.

`backend/decentralized/simulation.py` was one 1,542-line module. Splitting it
introduced the risk this test closes: five modules with two shared mutable
registries (`EXECUTORS`, `NODE_HEALTH`) and a layer order that has to stay
acyclic, because a cycle would only fail for whoever imported the "wrong"
module first.

Python caches modules per process, so each check runs in its own interpreter:
"import this module first, with nothing else preloaded".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SIMULATION_MODULES = [
    "backend.decentralized.simulation.types",
    "backend.decentralized.simulation.health",
    "backend.decentralized.simulation.executors",
    "backend.decentralized.simulation.runner",
    "backend.decentralized.simulation.jobs",
    "backend.decentralized.simulation",
]


@pytest.mark.parametrize("module", SIMULATION_MODULES)
def test_module_imports_first(module: str):
    """Importing this module first must not pull a half-initialised sibling."""
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"{module} cannot be imported first:\n{proc.stdout}\n{proc.stderr}"
    )


def test_package_still_exposes_the_whole_surface():
    """Callers use `simulation.<name>`; the package must keep answering."""
    from backend.decentralized import simulation

    for name in (
        # types
        "SimulationSpec", "SimulationConfig", "SimulationError",
        "ExecutorError", "JOB_KINDS", "TERMINAL_STATUSES", "MAX_STEPS",
        # health
        "node_health", "reset_node_health", "reputation", "REPUTATION_FLOOR",
        # executors
        "SimulationExecutor", "DeterministicExecutor", "DcmWasmExecutor",
        "FaultInjectingExecutor", "register_executor", "unregister_executor",
        "available_nodes", "default_registry",
        # runner
        "quote", "verify_replicas", "run_job", "execution_hash",
        # jobs
        "submit", "save_job", "get_job", "list_jobs", "job_totals",
        "enqueue_job", "recover_interrupted_jobs", "SimulationWorker",
        "simulation_bundle", "STATUS_QUEUED", "OPEN_STATUSES",
        # the two live registries
        "EXECUTORS", "NODE_HEALTH",
    ):
        assert hasattr(simulation, name), f"simulation lost {name}"


def test_live_registries_are_not_snapshots():
    """`simulation.EXECUTORS` must be the dict `executors` actually writes to.

    A `from ... import EXECUTORS` in the package __init__ would bind one dict
    object at import time; rebinding `executors.EXECUTORS` (test isolation, or
    an embedder injecting nodes) would then leave the package name stale. The
    package resolves these two dynamically for exactly that reason.
    """
    from backend.decentralized import simulation
    from backend.decentralized.simulation import executors, health

    assert simulation.EXECUTORS is executors.EXECUTORS
    assert simulation.NODE_HEALTH is health.NODE_HEALTH

    # And a rebind is visible through the package, as it was pre-split.
    original = executors.EXECUTORS
    try:
        executors.EXECUTORS = {}
        assert simulation.EXECUTORS == {}
    finally:
        executors.EXECUTORS = original


def test_unknown_attribute_raises_attribute_error():
    from backend.decentralized import simulation

    with pytest.raises(AttributeError):
        simulation.no_such_name  # noqa: B018