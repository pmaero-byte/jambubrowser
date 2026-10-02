"""Durable simulation queue: enqueue -> claim -> settle, survive a restart."""

import asyncio

import pytest

from backend.decentralized import simulation

from tests.test_simulation import run, spec  # reuse helpers


@pytest.fixture(autouse=True)
def _clean_store():
    from backend.core.database import get_db

    def _clear():
        with get_db() as conn:
            conn.execute("DELETE FROM simulation_jobs")
            conn.execute("DELETE FROM worker_verdicts")
            conn.commit()

    _clear()
    yield
    _clear()


def _register(*ids):
    for nid in ids:
        simulation.register_executor(simulation.DeterministicExecutor(nid))


def test_enqueue_writes_queued_row():
    _register("n1")
    job = simulation.enqueue_job(spec(), replicates=1, idempotency_key="k1")
    assert job["status"] == simulation.STATUS_QUEUED
    assert job["charged_dct"] == 0.0
    stored = simulation.get_job(job["id"])
    assert stored["status"] == simulation.STATUS_QUEUED


def test_enqueue_is_idempotent_before_dispatch():
    _register("n1")
    first = simulation.enqueue_job(spec(), replicates=1, idempotency_key="k1")
    again = simulation.enqueue_job(spec(), replicates=1, idempotency_key="k1")
    assert again["id"] == first["id"]
    assert again["idempotent_replay"] is True


def test_claim_flips_to_running_once():
    _register("n1")
    job = simulation.enqueue_job(spec(), replicates=1)
    row = simulation._claim_next_queued()
    assert row is not None and row["id"] == job["id"]
    assert simulation.get_job(job["id"])["status"] == simulation.STATUS_RUNNING
    assert simulation._claim_next_queued() is None


def test_recover_interrupted_jobs_requeues_running():
    _register("n1")
    job = simulation.enqueue_job(spec(), replicates=1)
    simulation._claim_next_queued()
    assert simulation.get_job(job["id"])["status"] == simulation.STATUS_RUNNING
    assert simulation.recover_interrupted_jobs() == 1
    assert simulation.get_job(job["id"])["status"] == simulation.STATUS_QUEUED


def test_worker_run_one_settles():
    _register("n1", "n2")
    job = simulation.enqueue_job(spec(), replicates=2)
    worker = simulation.SimulationWorker(poll=0.01)
    did = run(worker.run_one())
    assert did is True
    stored = simulation.get_job(job["id"])
    assert stored["status"] == simulation.STATUS_SETTLED
    assert stored["charged_dct"] > 0


def test_worker_survives_executor_crash():
    simulation.register_executor(simulation.DeterministicExecutor("n1"))
    job = simulation.enqueue_job(spec(), replicates=1)
    # Break the spec stored in the row so run_job raises inside the worker.
    from backend.core.database import get_db

    with get_db() as conn:
        conn.execute(
            "UPDATE simulation_jobs SET spec_json = ? WHERE id = ?",
            ('{"kind": "no-such-kind", "module": "x"}', job["id"]),
        )
        conn.commit()
    worker = simulation.SimulationWorker(poll=0.01)
    did = run(worker.run_one())
    assert did is True
    stored = simulation.get_job(job["id"])
    assert stored["status"] == simulation.STATUS_FAILED
    assert stored["charged_dct"] == 0.0


def test_worker_loop_processes_then_stops():
    _register("n1")
    simulation.enqueue_job(spec(), replicates=1)
    worker = simulation.SimulationWorker(poll=0.01)

    async def drive():
        await worker.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            jobs = simulation.list_jobs(limit=5)
            if jobs and jobs[0]["status"] == simulation.STATUS_SETTLED:
                break
        await worker.stop()
        return simulation.list_jobs(limit=5)[0]

    settled = run(drive())
    assert settled["status"] == simulation.STATUS_SETTLED
