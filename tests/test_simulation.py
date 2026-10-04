"""
Tests for decentralised simulation compute.

Covers the three defects this feature existed to fix:

1. **Numeric verification** — solver output must be compared as numbers, not
   as strings (``difflib`` calls "100.0" and "1.000" ~90% similar).
2. **Simulation providers get paid** — MeshPay used to key entitlement on
   ``kind == "usage"`` only, so a node that only ran simulations earned $0.
3. **A real job surface** — frozen spec hash, DCT→tier bridging, idempotent
   submits, and the rule that disagreement is quarantined rather than paid.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.decentralized import simulation
from backend.decentralized.meshpay import hash_receipt
from backend.decentralized.meshpay.plan import (
    group_epochs,
    is_provider_reward,
    payout_plan,
    reconcile_window,
)
from backend.decentralized.verification import (
    COMPARATORS,
    TOLERANCE_COMPARATORS,
    numeric_report,
    parse_numeric,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def registry(monkeypatch):
    """Every test starts from a known, empty node registry and health state.

    ``NODE_HEALTH`` is a module global for the same reason ``EXECUTORS`` is:
    the scheduler is stateless-per-request but the fleet is process-wide. A
    node quarantined by one test would otherwise be unavailable to the next.

    Both now live in the submodule that owns them — ``executors.EXECUTORS`` and
    ``health.NODE_HEALTH`` — because every reader and mutator of each is in that
    one file. Patching the package attribute would rebind a name nothing reads.
    """
    from backend.decentralized.simulation import executors, health

    monkeypatch.setattr(executors, "EXECUTORS", {})
    monkeypatch.setattr(health, "NODE_HEALTH", {})
    yield
    executors.EXECUTORS.clear()
    simulation.reset_node_health()


@pytest.fixture(autouse=True)
def evidence_key(monkeypatch):
    monkeypatch.setenv("JAMBU_EVIDENCE_KEY", "77" * 32)


@pytest.fixture(autouse=True)
def clean_store():
    """Empty the job + verdict tables per test.

    The suite runs against a shared in-memory SQLite connection, so rows
    written by an earlier test would leak into later assertions. Both tables
    matter: ``simulation_jobs`` for spend/counts, and ``worker_verdicts``
    because reputation is derived from those scorecards.
    """
    from backend.core.database import get_db

    def _clear():
        with get_db() as conn:
            conn.execute("DELETE FROM simulation_jobs")
            conn.execute("DELETE FROM worker_verdicts")
            conn.commit()

    _clear()
    yield
    _clear()


def spec(**kw):
    defaults = dict(kind="native", module="heat", seed=7, steps=200)
    defaults.update(kw)
    return simulation.SimulationSpec.create(**defaults)


def run(coro):
    """Repo convention for async tests (pytest-asyncio is not a dep)."""
    return asyncio.run(coro)


def make_entry(kind: str, prev: str | None, **fields) -> dict:
    entry = {"kind": kind, "timestamp": 1700000000000, "prevInvoiceHash": prev}
    entry.update(fields)
    entry["invoiceHash"] = hash_receipt(entry)
    return entry


# ---------------------------------------------------------------------------
# 1. Numeric verification — the comparator that makes simulation checks mean
#    something
# ---------------------------------------------------------------------------

class TestNumericComparator:
    def test_registered_and_tolerance_aware(self):
        assert "numeric" in COMPARATORS
        assert "numeric" in TOLERANCE_COMPARATORS

    def test_magnitude_difference_is_not_similarity(self):
        """The exact case difflib gets wrong: text-similar, value-different."""
        from difflib import SequenceMatcher

        # A 10x difference in the value scores 0.909 on difflib, which sails
        # past the default 0.85 gate. A solver off by an order of magnitude
        # would be certified as a MATCH.
        naive = SequenceMatcher(None, "100.0", "1000.0").ratio()
        assert naive > 0.85, "premise: difflib rates these a pass"
        ok, agreement = COMPARATORS["numeric"]("100.0", "1000.0")
        assert ok is False
        assert agreement <= 0.1

    def test_order_of_magnitude_error_is_caught(self):
        for pair in [("100.0", "1000.0"), ("1.0", "10.0"), ("0.5", "5.0")]:
            ok, _ = COMPARATORS["numeric"](*pair)
            assert ok is False, pair

    def test_identical_payloads_match(self):
        a = json.dumps({"residual": 0.99812, "iterations": 250, "t_ms": 812.4})
        assert numeric_report(a, a)["verdict"] == "MATCH"

    def test_within_tolerance_matches(self):
        a = json.dumps({"residual": 0.99812})
        b = json.dumps({"residual": 0.99812 + 1e-13})
        assert numeric_report(a, b)["verdict"] == "MATCH"

    def test_drift_beyond_tolerance_is_a_mismatch(self):
        a = json.dumps({"residual": 0.99812, "t_ms": 812.4})
        b = json.dumps({"residual": 0.99812, "t_ms": 813.0})
        report = numeric_report(a, b)
        assert report["verdict"] == "MISMATCH"
        assert report["max_deviation_path"] == "$.t_ms"

    def test_truncated_replica_is_a_mismatch(self):
        """Dropping a numeric field is divergence even if shared values agree."""
        a = json.dumps({"residual": 0.99812, "iterations": 250})
        b = json.dumps({"residual": 0.99812})
        report = numeric_report(a, b)
        assert report["verdict"] == "MISMATCH"
        assert report["only_in_primary"] == ["$.iterations"]

    def test_nested_deviation_reports_its_path(self):
        a = json.dumps({"trace": {"x": [1, 2, 3]}})
        b = json.dumps({"trace": {"x": [1, 2, 9]}})
        assert numeric_report(a, b)["max_deviation_path"] == "$.trace.x[2]"

    def test_prose_is_never_numerically_verified(self):
        report = numeric_report("done ok", "done fine")
        assert report["verdict"] == "ERROR"
        assert report["ok"] is False

    def test_bools_are_not_numbers(self):
        assert parse_numeric('{"a": true}')["ok"] is False

    def test_invalid_json_is_an_error_not_a_pass(self):
        assert numeric_report('{"a": 1,}', '{"a": 1}')["verdict"] == "ERROR"

    def test_empty_output_is_an_error(self):
        assert numeric_report("", "")["verdict"] == "ERROR"

    def test_nan_and_inf_are_rejected(self):
        assert numeric_report('{"a": NaN}', '{"a": NaN}')["verdict"] == "ERROR"

    def test_flat_numeric_text_compares(self):
        assert numeric_report("1.0 2.0 3.0", "1.0 2.0 3.0")["verdict"] == "MATCH"

    def test_sweep_detects_single_sample_drift(self):
        base = [round(i / 1000, 9) for i in range(500)]
        drifted = list(base)
        drifted[317] += 1e-6
        report = numeric_report(json.dumps(base), json.dumps(drifted))
        assert report["verdict"] == "MISMATCH"
        assert report["max_deviation_path"] == "$[317]"


# ---------------------------------------------------------------------------
# 2. Money: simulation providers are actually paid
# ---------------------------------------------------------------------------

class TestSimulationProviderPayouts:
    def test_provider_reward_is_shape_based(self):
        # Whatever DCM calls the kind, nodeId + reward means it pays out.
        for kind in ("usage", "simulation-usage", "wasm-usage", "sim-reward"):
            assert is_provider_reward(
                {"kind": kind, "nodeId": "n1", "reward": 0.5}
            ) is True

    def test_settlement_is_not_double_counted(self):
        assert is_provider_reward(
            {"kind": "settlement", "nodeId": "n1", "reward": 5}
        ) is False

    def test_dense_receipt_is_not_a_payment(self):
        assert is_provider_reward(
            {"kind": "dense-receipt", "nodeId": "n1", "reward": 5}
        ) is False

    def test_zero_reward_and_missing_node_are_excluded(self):
        assert is_provider_reward(
            {"kind": "usage", "nodeId": "n", "reward": 0}
        ) is False
        assert is_provider_reward({"kind": "usage", "reward": 5}) is False

    def test_simulation_only_node_receives_a_payout(self):
        """The regression: this node used to accrue exactly $0."""
        entries, prev = [], None
        for i in range(2):
            e = make_entry("simulation-usage", prev, nodeId="sim-1",
                           reward=0.05 * (i + 1), executionHash=f"sim{i}")
            entries.append(e)
            prev = e["invoiceHash"]
        plan = payout_plan(entries, epoch_size=50)
        provider = plan["providers"][0]
        assert provider["nodeId"] == "sim-1"
        assert provider["grossDct"] == pytest.approx(0.15)
        assert provider["usdc"] > 0
        assert provider["rewardByKind"] == {"simulation-usage": 0.15}

    def test_already_settled_is_reported_separately(self):
        entries = [
            make_entry("usage", None, nodeId="n1", reward=0.1),
            make_entry("settlement", "x", nodeId="n1", netReward=0.1),
        ]
        plan = payout_plan(entries, epoch_size=50)
        assert plan["providers"][0]["grossDct"] == pytest.approx(0.1)
        assert plan["already_settled_dct"] == {"n1": 0.1}

    def test_charge_is_split_by_kind(self):
        entries = [
            make_entry("inference-charge", None, chargedDct=0.2, did="d"),
            make_entry("simulation-charge", "x", chargedDct=0.5, did="d"),
        ]
        metering = reconcile_window(entries)
        assert metering["by_kind"]["inference-charge"]["chargedDct"] == 0.2
        assert metering["by_kind"]["simulation-charge"]["chargedDct"] == 0.5
        assert metering["totalChargedDct"] == 0.7

    def test_billed_but_unpaid_work_is_flagged(self):
        entries = [
            make_entry("simulation-charge", None, chargedDct=0.5,
                       executionHash="job-1"),
            make_entry("simulation-usage", "x", nodeId="n1", reward=0.1,
                       executionHash="job-2"),
        ]
        metering = reconcile_window(entries)
        assert metering["correlated"] is True
        assert metering["unmatchedChargeIds"] == ["job-1"]

    def test_uncorrelated_receipts_do_not_claim_a_match(self):
        metering = reconcile_window(
            [make_entry("simulation-charge", None, chargedDct=0.5)]
        )
        assert metering["correlated"] is False
        assert metering["unmatchedChargeCount"] == 0
        assert "aggregate" in metering["note"]

    def test_epoch_carries_metering(self):
        epochs = group_epochs(
            [make_entry("usage", None, nodeId="n", reward=1)], epoch_size=50
        )
        assert "metering" in epochs[0]


# ---------------------------------------------------------------------------
# 3. Spec freeze + quoting
# ---------------------------------------------------------------------------

class TestSpecAndQuote:
    def test_spec_hash_is_stable(self):
        assert spec().spec_hash() == spec().spec_hash()

    def test_every_answer_affecting_field_changes_the_hash(self):
        base = spec().spec_hash()
        for field, value in [
            ("module", "fluid"), ("seed", 8), ("steps", 201),
            ("kind", "wasm"), ("params", {"amplitude": 2.0}),
            ("module_digest", "a" * 64),
        ]:
            assert spec(**{field: value}).spec_hash() != base, field

    def test_module_digest_pins_the_artefact(self):
        """Different code, different spec — so replicas stay comparable."""
        assert spec(module_digest="a" * 64).spec_hash() != spec(
            module_digest="b" * 64
        ).spec_hash()

    @pytest.mark.parametrize("kwargs", [
        {"module": ""}, {"module": "   "}, {"kind": "nope"},
        {"steps": 0}, {"steps": 10**9}, {"timeout_ms": 1},
        {"module_digest": "zz"}, {"module_digest": "a" * 63},
    ])
    def test_invalid_specs_are_rejected(self, kwargs):
        with pytest.raises(simulation.SimulationError):
            spec(**kwargs)

    def test_spec_round_trips_through_its_canonical_form(self):
        original = spec(params={"amplitude": 3.0}, module_digest="c" * 64)
        restored = simulation.SimulationSpec.from_dict(original.canonical())
        assert restored.spec_hash() == original.spec_hash()

    def test_quote_breakdown_is_additive(self):
        q = simulation.quote(spec(steps=1000), replicates=3)
        cfg = simulation.SimulationConfig.from_env()
        assert q["breakdownDct"]["base"] == cfg.base_dct
        assert q["breakdownDct"]["steps"] == pytest.approx(1000 * cfg.per_step_dct)
        assert q["breakdownDct"]["replicas"] == pytest.approx(2 * cfg.per_replica_dct)
        assert q["dct"] == pytest.approx(
            cfg.base_dct + 1000 * cfg.per_step_dct + 2 * cfg.per_replica_dct,
            rel=1e-6,
        )

    def test_quote_scales_with_work(self):
        assert simulation.quote(spec(steps=10_000))["dct"] > simulation.quote(
            spec(steps=1_000)
        )["dct"]

    def test_extra_replicas_cost_more(self):
        assert simulation.quote(spec(), replicates=3)["dct"] > simulation.quote(
            spec(), replicates=1
        )["dct"]

    def test_tier_follows_value(self, monkeypatch):
        monkeypatch.setenv("JAMBU_VERIFY_REDUNDANT_ABOVE_USDC", "0.0001")
        assert simulation.quote(spec(steps=1000))["tier"] == "REDUNDANT"

    def test_quote_rejects_bad_replica_count(self):
        with pytest.raises(simulation.SimulationError):
            simulation.quote(spec(), replicates=0)

    def test_quote_carries_a_rate_note(self):
        assert "not an oracle" in simulation.quote(spec())["rate_note"]


# ---------------------------------------------------------------------------
# 4. Dispatch + verification
# ---------------------------------------------------------------------------

class BrokenExecutor(simulation.SimulationExecutor):
    node_id = "broken"

    async def execute(self, spec):
        raise simulation.ExecutorError("wasm runtime missing")


def two_nodes():
    simulation.register_executor(simulation.DeterministicExecutor("node-a"))
    simulation.register_executor(simulation.DeterministicExecutor("node-b"))


class TestDispatch:
    def test_agreeing_replicas_settle_and_charge(self):
        two_nodes()
        result = run(simulation.run_job(spec(), replicates=2))
        assert result["status"] == simulation.STATUS_SETTLED
        assert result["charged_dct"] > 0
        assert result["nodes_used"] == ["node-a", "node-b"]
        assert result["verification"]["verdict"] == "MATCH"

    def test_drifting_replica_is_quarantined_and_unpaid(self):
        """The core safety property: disagreement never becomes revenue."""
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(
            simulation.FaultInjectingExecutor("node-b", scale=1.002)
        )
        result = run(simulation.run_job(spec(), replicates=2))
        assert result["status"] == simulation.STATUS_QUARANTINED
        assert result["charged_dct"] == 0.0
        assert result["verification"]["verdict"] == "MISMATCH"
        assert result["verification"]["max_deviation_path"]

    def test_replicas_are_bit_identical_when_honest(self):
        two_nodes()
        result = run(simulation.run_job(spec(steps=500), replicates=2))
        outputs = [a["output"] for a in result["attempts"] if a["ok"]]
        assert outputs[0] == outputs[1]

    def test_dropped_field_is_caught(self):
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(
            simulation.FaultInjectingExecutor("node-b", drop_field="mean_state")
        )
        result = run(simulation.run_job(spec(), replicates=2))
        assert result["verification"]["verdict"] == "MISMATCH"
        assert result["charged_dct"] == 0.0

    def test_execution_hash_binds_spec_node_and_output(self):
        two_nodes()
        result = run(simulation.run_job(spec(), replicates=2))
        hashes = {a["node_id"]: a["execution_hash"] for a in result["attempts"]}
        assert len(set(hashes.values())) == 2, "different nodes -> different hashes"
        again = run(simulation.run_job(spec(), replicates=2))
        again_hashes = {a["node_id"]: a["execution_hash"] for a in again["attempts"]}
        assert again_hashes == hashes, "same spec -> reproducible hashes"

    def test_redundant_tier_refuses_a_single_node(self, monkeypatch):
        monkeypatch.setenv("JAMBU_VERIFY_REDUNDANT_ABOVE_USDC", "0.0001")
        simulation.register_executor(simulation.DeterministicExecutor("only"))
        result = run(simulation.run_job(spec(steps=1000), replicates=1))
        assert result["status"] == simulation.STATUS_QUARANTINED
        assert result["charged_dct"] == 0.0
        assert result["required_tier_satisfied"] is False
        assert "needs 2 available node(s)" in result["error"]

    def test_policy_overrides_a_single_replica_request(self, monkeypatch):
        monkeypatch.setenv("JAMBU_VERIFY_REDUNDANT_ABOVE_USDC", "0.0001")
        two_nodes()
        result = run(simulation.run_job(spec(steps=1000), replicates=1))
        assert len(result["nodes_used"]) == 2, "policy forces the second node"

    def test_node_failure_is_reported_not_swallowed(self):
        simulation.register_executor(BrokenExecutor())
        simulation.register_executor(simulation.DeterministicExecutor("good"))
        result = run(simulation.run_job(spec(), replicates=2))
        broken = next(a for a in result["attempts"] if a["node_id"] == "broken")
        assert broken["ok"] is False
        assert "wasm runtime missing" in broken["error"]
        assert result["status"] == simulation.STATUS_QUARANTINED
        assert result["charged_dct"] == 0.0

    def test_every_node_failing_is_a_failure(self):
        simulation.register_executor(BrokenExecutor())
        result = run(simulation.run_job(spec(), replicates=1))
        assert result["status"] == simulation.STATUS_FAILED
        assert result["charged_dct"] == 0.0

    def test_unknown_kind_fails_clearly(self):
        class InferenceOnly(simulation.SimulationExecutor):
            node_id = "inf-only"
            supports = ("inference",)

            async def execute(self, spec):
                return {}

        simulation.register_executor(InferenceOnly())
        result = run(simulation.run_job(spec(kind="wasm"), replicates=1))
        assert result["status"] == simulation.STATUS_FAILED
        assert "no registered node" in result["error"]

    def test_timeout_is_captured(self):
        class Slow(simulation.SimulationExecutor):
            node_id = "slow"

            async def execute(self, spec):
                import asyncio

                await asyncio.sleep(5)
                return {"x": 1}

        simulation.register_executor(Slow())
        result = run(simulation.run_job(spec(timeout_ms=150), replicates=1))
        assert result["status"] == simulation.STATUS_FAILED
        assert "timeout" in result["attempts"][0]["error"]

    def test_registry_reports_nodes_by_kind(self):
        two_nodes()
        assert [n["node_id"] for n in simulation.available_nodes("native")] == [
            "node-a", "node-b",
        ]
        assert simulation.available_nodes("nonsense") == []

    def test_executor_registration_round_trips(self):
        simulation.register_executor(simulation.DeterministicExecutor("x"))
        assert "x" in simulation.EXECUTORS
        assert simulation.unregister_executor("x") is True
        assert simulation.unregister_executor("x") is False

    def test_execution_hash_changes_with_output(self):
        base = simulation.execution_hash("spec", "node", {"v": 1})
        assert simulation.execution_hash("spec", "node", {"v": 2}) != base
        assert simulation.execution_hash("spec", "other", {"v": 1}) != base
        assert simulation.execution_hash("other", "node", {"v": 1}) != base


# ---------------------------------------------------------------------------
# 4b. Consensus: the mesh decides, not the first responder
# ---------------------------------------------------------------------------

def three_nodes(drifting: str = ""):
    for name in ("node-a", "node-b", "node-c"):
        if name == drifting:
            simulation.register_executor(
                simulation.FaultInjectingExecutor(name, scale=1.002)
            )
        else:
            simulation.register_executor(simulation.DeterministicExecutor(name))


class TestConsensus:
    def test_all_agree_is_a_plain_match(self):
        three_nodes()
        result = run(simulation.run_job(spec(), replicates=3))
        verification = result["verification"]
        assert verification["verdict"] == "MATCH"
        assert verification["majority"] is True
        assert verification["diverging"] == []
        assert verification["agreeing"] == ["node-a", "node-b", "node-c"]

    def test_majority_settles_and_names_the_outlier(self):
        """Three replicas make replication worth paying for: one bad node
        must not block a correct answer."""
        three_nodes(drifting="node-c")
        result = run(simulation.run_job(spec(), replicates=3))
        verification = result["verification"]
        assert verification["verdict"] == "MATCH_CONSENSUS"
        assert verification["agreeing"] == ["node-a", "node-b"]
        assert verification["diverging"] == ["node-c"]
        assert result["status"] == simulation.STATUS_SETTLED
        assert result["charged_dct"] > 0

    def test_outlier_is_penalised_not_the_majority(self):
        three_nodes(drifting="node-c")
        run(simulation.run_job(spec(), replicates=3))
        assert simulation.node_health("node-c").divergences == 1
        assert simulation.node_health("node-a").divergences == 0
        assert simulation.node_health("node-b").divergences == 0

    def test_two_node_tie_blames_nobody(self):
        """With n=2 the median sits exactly between two different answers, so
        both 'deviate' by construction. Reporting that as two divergences would
        be a lie and would poison both nodes' reputation."""
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(
            simulation.FaultInjectingExecutor("node-b", scale=1.002)
        )
        result = run(simulation.run_job(spec(), replicates=2))
        verification = result["verification"]
        assert verification["verdict"] == "MISMATCH"
        assert verification["disputed"] == ["node-a", "node-b"]
        assert verification["diverging"] == []
        assert result["charged_dct"] == 0.0
        assert simulation.node_health("node-a").divergences == 0
        assert simulation.node_health("node-b").divergences == 0

    def test_tie_is_reported_as_unattributable(self):
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(
            simulation.FaultInjectingExecutor("node-b", scale=1.002)
        )
        result = run(simulation.run_job(spec(), replicates=2))
        assert "cannot attribute" in result["verification"]["reason"]

    def test_single_replica_settles_but_says_it_cannot_cross_check(self):
        simulation.register_executor(simulation.DeterministicExecutor("only"))
        result = run(simulation.run_job(spec(), replicates=1))
        assert result["status"] == simulation.STATUS_SETTLED
        assert result["verification"]["verdict"] == "MATCH"
        assert "nothing to cross-check" in result["verification"]["note"]

    def test_consensus_does_not_privilege_the_first_responder(self):
        """The regression that motivated consensus: node-c drifts and node-a
        is alphabetically first, so 'first answers = truth' would have
        quarantined the correct answer."""
        three_nodes(drifting="node-c")
        result = run(simulation.run_job(spec(), replicates=3))
        assert result["verification"]["diverging"] == ["node-c"]
        assert result["charged_dct"] > 0

    def test_dropped_field_makes_a_node_diverge_not_dispute(self):
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(simulation.DeterministicExecutor("node-b"))
        simulation.register_executor(
            simulation.FaultInjectingExecutor("node-c", drop_field="mean_state")
        )
        result = run(simulation.run_job(spec(), replicates=3))
        verification = result["verification"]
        assert verification["diverging"] == ["node-c"]
        outlier = next(
            c for c in verification["comparisons"] if c["node"] == "node-c"
        )
        assert "$.mean_state" in outlier["missing_vs_consensus"]

    def test_median_resists_one_extreme_outlier(self):
        medians = simulation._path_medians(
            [{"v": 1.0}, {"v": 1.0000001}, {"v": 900.0}]
        )
        assert medians["$.v"] == pytest.approx(1.0000001)


# ---------------------------------------------------------------------------
# 4c. Node health + quarantine
# ---------------------------------------------------------------------------

class CrashingExecutor(simulation.SimulationExecutor):
    node_id = "crashing"

    async def execute(self, spec):
        raise simulation.ExecutorError("runtime crashed")


class TestNodeHealth:
    def test_failure_below_threshold_does_not_quarantine(self):
        health = simulation.NodeHealth("n")
        health.record_failure("boom", threshold=2)
        assert health.available is True
        assert health.consecutive_failures == 1

    def test_threshold_triggers_quarantine(self):
        health = simulation.NodeHealth("n")
        for _ in range(2):
            health.record_failure("boom", threshold=2)
        assert health.available is False
        assert health.quarantined is True

    def test_success_clears_the_streak(self):
        health = simulation.NodeHealth("n")
        health.record_failure("boom", threshold=2)
        health.record_success(1.0)
        assert health.consecutive_failures == 0
        assert health.available is True

    def test_quarantine_expires_so_it_self_heals(self):
        health = simulation.NodeHealth("n")
        health.record_failure("boom", threshold=1)
        assert health.available is False
        health.quarantined_until = 0.0  # the window passes
        assert health.available is True, "must recover without a restart"

    def test_latency_is_an_ewma(self):
        health = simulation.NodeHealth("n")
        health.record_success(100.0)
        health.record_success(200.0)
        assert 100.0 < health.latency_ms < 200.0

    def test_quarantined_node_is_excluded_from_dispatch(self):
        simulation.register_executor(CrashingExecutor())
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(simulation.DeterministicExecutor("node-b"))
        simulation.node_health("crashing").record_failure("boom", threshold=1)

        result = run(simulation.run_job(spec(), replicates=2))
        assert "crashing" not in result["nodes_used"]
        assert "crashing" in result["excluded_nodes"]
        assert result["status"] == simulation.STATUS_SETTLED

    def test_fleet_routes_around_a_persistently_broken_node(self):
        """The real-use-case payoff: after a few bad jobs the mesh stops
        handing work to the broken node instead of burning every job."""
        simulation.register_executor(CrashingExecutor())
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(simulation.DeterministicExecutor("node-b"))
        threshold = simulation.SimulationConfig.from_env().failure_threshold

        for _ in range(threshold + 1):
            run(simulation.run_job(spec(), replicates=2))

        assert simulation.node_health("crashing").available is False
        final = run(simulation.run_job(spec(), replicates=2))
        assert final["status"] == simulation.STATUS_SETTLED
        assert final["charged_dct"] > 0
        assert "crashing" not in final["nodes_used"]

    def test_to_dict_is_json_safe(self):
        health = simulation.NodeHealth("n")
        health.record_failure("boom", threshold=1)
        payload = health.to_dict()
        assert payload["quarantined"] is True
        assert payload["last_error"] == "boom"
        assert payload["available"] is False

    def test_node_health_is_shared_per_node(self):
        assert simulation.node_health("x") is simulation.node_health("x")
        simulation.reset_node_health()
        assert simulation.node_health("x").failures == 0


class TestReputation:
    def test_fresh_node_ranks_below_proven_good(self):
        ordered = simulation._candidate_order(
            [
                simulation.DeterministicExecutor("aaa-fresh"),
                simulation.DeterministicExecutor("zzz-proven-good"),
            ],
            {
                "aaa-fresh": {"score": None},
                "zzz-proven-good": {"score": 1.0},
            },
        )
        assert [e.node_id for e in ordered] == ["zzz-proven-good", "aaa-fresh"]

    def test_fresh_node_ranks_above_proven_bad(self):
        """Absence of evidence is not evidence of badness."""
        ordered = simulation._candidate_order(
            [
                simulation.DeterministicExecutor("aaa-bad"),
                simulation.DeterministicExecutor("zzz-fresh"),
            ],
            {"aaa-bad": {"score": 0.0}, "zzz-fresh": {"score": None}},
        )
        assert [e.node_id for e in ordered] == ["zzz-fresh", "aaa-bad"]

    def test_ordering_is_deterministic_for_equal_scores(self):
        nodes = [
            simulation.DeterministicExecutor("c"),
            simulation.DeterministicExecutor("a"),
            simulation.DeterministicExecutor("b"),
        ]
        once = [e.node_id for e in simulation._candidate_order(nodes, {})]
        twice = [
            e.node_id
            for e in simulation._candidate_order(list(reversed(nodes)), {})
        ]
        assert once == twice == ["a", "b", "c"]

    def test_diverging_node_is_deprioritised_on_later_jobs(self):
        """Reputation has to change dispatch, not merely be reported."""
        three_nodes(drifting="node-c")
        first = run(simulation.run_job(spec(), replicates=2))
        assert "node-c" not in first["nodes_used"]
        second = run(simulation.run_job(spec(), replicates=2))
        assert "node-c" not in second["nodes_used"]

    def test_reputation_reflects_recorded_agreement(self):
        three_nodes(drifting="node-c")
        run(simulation.run_job(spec(), replicates=3))
        scores = simulation.reputation()
        assert scores["node-a"]["score"] == pytest.approx(1.0)
        assert scores["node-c"]["score"] == pytest.approx(0.0)
        assert scores["node-c"]["trusted"] is False
        assert scores["node-a"]["trusted"] is True

    def test_reputation_is_empty_for_a_fresh_mesh(self):
        assert simulation.reputation() == {}

    def test_simulation_traffic_reaches_the_shared_scorecards(self):
        """Without this the existing /verification/workers surface would be
        permanently blank for a mesh that only runs simulation jobs."""
        from backend.decentralized.verification import worker_report

        three_nodes()
        run(simulation.run_job(spec(), replicates=2))
        workers = {w["worker_id"]: w for w in worker_report()["workers"]}
        assert workers["node-a"]["redundancy_runs"] >= 1
        assert workers["node-a"]["redundancy_matches"] >= 1


# ---------------------------------------------------------------------------
# 5. Persistence, idempotency, evidence
# ---------------------------------------------------------------------------

class TestJobStore:
    def test_submit_persists_and_is_retrievable(self):
        two_nodes()
        job = run(simulation.submit(spec(), replicates=2))
        fetched = simulation.get_job(job["id"])
        assert fetched["status"] == job["status"]
        assert fetched["spec_hash"] == job["spec_hash"]
        assert fetched["nodes_used"] == job["nodes_used"]

    def test_idempotent_retry_never_double_charges(self):
        two_nodes()
        first = run(simulation.submit(spec(), replicates=2, idempotency_key="order-1"))
        second = run(simulation.submit(spec(), replicates=2, idempotency_key="order-1"))
        assert first["id"] == second["id"]
        assert second["idempotent_replay"] is True
        assert second["charged_dct"] == first["charged_dct"]
        assert len(simulation.list_jobs()) == 1, "no second job billed"

    def test_distinct_keys_create_distinct_jobs(self):
        two_nodes()
        run(simulation.submit(spec(), idempotency_key="a"))
        run(simulation.submit(spec(), idempotency_key="b"))
        assert len(simulation.list_jobs()) == 2

    def test_persist_false_skips_the_store(self):
        two_nodes()
        job = run(simulation.submit(spec(), persist=False))
        assert job["id"]
        assert simulation.list_jobs() == []

    def test_list_jobs_filters(self):
        two_nodes()
        run(simulation.submit(spec(seed=1)))
        run(simulation.submit(spec(seed=2)))
        assert len(simulation.list_jobs(status="SETTLED")) == 2
        assert simulation.list_jobs(status="QUARANTINED") == []
        target = simulation.list_jobs()[0]["spec_hash"]
        assert all(
            j["spec_hash"] == target for j in simulation.list_jobs(spec_hash=target)
        )

    def test_totals_separate_settled_from_unpaid(self):
        two_nodes()
        run(simulation.submit(spec(seed=1), replicates=2))
        # Replicate so the drifting node is actually asked — a single-replica
        # job has nobody to disagree with it.
        simulation.register_executor(
            simulation.FaultInjectingExecutor("node-b", scale=1.5)
        )
        run(simulation.submit(spec(seed=2), replicates=2))
        totals = simulation.job_totals()
        assert totals["by_status"]["SETTLED"]["jobs"] == 1
        assert totals["by_status"]["QUARANTINED"]["jobs"] == 1
        assert totals["unpaidJobs"] == 1
        # Only the verified job contributes spend.
        assert totals["chargedDct"] == pytest.approx(
            simulation.quote(spec(seed=1), replicates=2)["dct"]
        )

    def test_unknown_job_is_none(self):
        assert simulation.get_job("nope") is None
        assert simulation.get_job_by_idempotency_key("nope") is None

    def test_evidence_bundle_verifies(self):
        from backend.decentralized.evidence import verify_bundle

        two_nodes()
        job = run(simulation.submit(spec(), replicates=2))
        bundle = simulation.simulation_bundle(job["id"])
        assert bundle["kind"] == "compute_simulation"
        assert verify_bundle(bundle)["valid"] is True
        payload = bundle["payload"]
        assert payload["spec_hash"] == job["spec_hash"]
        assert set(payload["execution_hashes"]) == {"node-a", "node-b"}
        assert payload["charged_dct"] > 0

    def test_evidence_bundle_for_unknown_job_raises(self):
        with pytest.raises(KeyError):
            simulation.simulation_bundle("nope")


# ---------------------------------------------------------------------------
# 6. Routes
# ---------------------------------------------------------------------------

class TestSimulationRoutes:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from backend.engine import app

        with TestClient(app) as c:
            yield c

    def test_config_lists_nodes(self, client):
        two_nodes()
        body = client.get("/simulation/config").json()
        assert [n["node_id"] for n in body["nodes"]] == ["node-a", "node-b"]
        assert "native" in body["kinds"]
        assert "MATCH_CONSENSUS" in body["verdicts"]
        assert "not an oracle" in body["config"]["rate_note"]

    def test_nodes_reports_health_and_reputation(self, client):
        three_nodes(drifting="node-c")
        # Divergence is only recorded once a job actually runs.
        client.post(
            "/simulation/submit", json={"module": "heat", "replicates": 3}
        )
        body = client.get("/simulation/nodes").json()
        assert body["count"] == 3
        assert body["available"] == 3
        assert body["quarantined"] == []
        by_id = {n["node_id"]: n for n in body["nodes"]}
        assert set(by_id) == {"node-a", "node-b", "node-c"}
        for node in by_id.values():
            assert "health" in node and "reputation" in node
        assert body["diverging"] == ["node-c"]
        assert by_id["node-c"]["reputation"]["divergences"] == 1
        # The honest nodes kept a perfect agreement rate.
        assert by_id["node-a"]["reputation"]["score"] == pytest.approx(1.0)

    def test_nodes_marks_quarantined_nodes_unavailable(self, client):
        simulation.register_executor(CrashingExecutor())
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.node_health("crashing").record_failure("boom", threshold=1)

        body = client.get("/simulation/nodes").json()
        assert body["available"] == 1
        assert body["quarantined"] == ["crashing"]
        # Dispatch order: healthy nodes first.
        assert body["nodes"][0]["health"]["available"] is True

    def test_nodes_filters_by_kind(self, client):
        class InferenceOnly(simulation.SimulationExecutor):
            node_id = "inf-only"
            supports = ("inference",)

            async def execute(self, spec):
                return {}

        simulation.register_executor(InferenceOnly())
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        # node-a serves every kind; inf-only serves inference only.
        assert client.get("/simulation/nodes?kind=native").json()["count"] == 1
        assert client.get("/simulation/nodes?kind=inference").json()["count"] == 2

    def test_nodes_is_empty_not_broken_on_a_fresh_mesh(self, client):
        body = client.get("/simulation/nodes").json()
        assert body["nodes"] == []
        assert body["count"] == 0

    def test_quote_dispatches_nothing(self, client):
        two_nodes()
        resp = client.post("/simulation/quote", json={"module": "heat", "steps": 500})
        assert resp.status_code == 200
        body = resp.json()
        assert body["dct"] > 0
        assert body["tier"] in ("SIGNED", "CANARY", "REDUNDANT", "ATTESTED")
        assert client.get("/simulation/jobs").json()["count"] == 0

    def test_submit_then_read_back(self, client):
        two_nodes()
        job = client.post(
            "/simulation/submit",
            json={"module": "heat", "steps": 300, "replicates": 2},
        ).json()
        assert job["status"] == "SETTLED"
        fetched = client.get(f"/simulation/jobs/{job['id']}").json()
        assert fetched["spec_hash"] == job["spec_hash"]
        assert fetched["charged_dct"] == job["charged_dct"]

    def test_submit_honours_idempotency_over_http(self, client):
        two_nodes()
        payload = {
            "module": "heat", "steps": 300, "replicates": 2,
            "idempotency_key": "abc",
        }
        first = client.post("/simulation/submit", json=payload).json()
        second = client.post("/simulation/submit", json=payload).json()
        assert first["id"] == second["id"]
        assert second["idempotent_replay"] is True
        assert client.get("/simulation/jobs").json()["count"] == 1

    def test_drift_over_http_is_quarantined(self, client):
        simulation.register_executor(simulation.DeterministicExecutor("node-a"))
        simulation.register_executor(
            simulation.FaultInjectingExecutor("node-b", scale=1.002)
        )
        job = client.post(
            "/simulation/submit", json={"module": "heat", "replicates": 2}
        ).json()
        assert job["status"] == "QUARANTINED"
        assert job["charged_dct"] == 0.0

    def test_evidence_endpoint(self, client):
        two_nodes()
        job = client.post("/simulation/submit", json={"module": "heat"}).json()
        resp = client.get(f"/simulation/jobs/{job['id']}/evidence")
        assert resp.status_code == 200
        assert resp.json()["kind"] == "compute_simulation"

    def test_unknown_job_is_404(self, client):
        assert client.get("/simulation/jobs/nope").status_code == 404
        assert client.get("/simulation/jobs/nope/evidence").status_code == 404

    @pytest.mark.parametrize("payload", [
        {"module": "   "},
        {"module": "heat", "kind": "nope"},
        {"module": "heat", "replicates": 0},
        {"module": "heat", "module_digest": "zz"},
        {"module": "heat", "steps": 0},
    ])
    def test_invalid_requests_are_422(self, client, payload):
        assert client.post("/simulation/submit", json=payload).status_code == 422

    def test_invalid_query_params_are_422(self, client):
        assert client.get("/simulation/jobs?limit=0").status_code == 422
        assert client.get("/simulation/jobs?limit=99999").status_code == 422
        assert client.get("/simulation/jobs?status=BOGUS").status_code == 422