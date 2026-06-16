#!/usr/bin/env python3
"""
HarnessX Phase 3+4 end-to-end smoke test.

Validates the full AEGIS + co-evolution pipeline with deterministic mock agents
and an injected heuristic planner (the mock LLM returns non-JSON echo text, so
the real Planner.propose would return [] and skip the evolve stage — we inject a
heuristic planner to exercise every stage deterministically).

Stages:
  1. Substitution algebra        — apply_edit / apply_edits / revert_edit / diff_configs / store
  2. Digester                    — failure traces -> FailureClusters
  3. Planner + Critic + Evolver  — clusters -> accepted edits -> variant config
  4. EnsembleRunner + EvolutionLoop — full AEGIS cycle (baseline -> evolve -> select)
  5. MixedPolicyBuffer + GRPOTrainer — cross-harness advantage + training signal
  6. CoEvolutionLoop             — alternating harness evolution + model training

Run:
    JAMBU_LLM_PROVIDER=mock python3 tests/smoke_harnessx_e2e.py

Exits 0 if every stage passes, 1 otherwise. Safe to run via pytest too.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from types import SimpleNamespace

# Ensure a hermetic, mock-LLM environment.
os.environ.setdefault("JAMBU_DB_PATH", ":memory:")
os.environ.setdefault("JAMBU_VAULT_KEY", "test-key-do-not-use-in-production-32bytes!")
os.environ.setdefault("JAMBU_LLM_PROVIDER", "mock")

# Running this file directly puts tests/ on sys.path; add the repo root so
# `backend` is importable (the rest of the suite is driven via pytest from root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# Imports (after env is set so the LLM registry initialises under mock)
# ---------------------------------------------------------------------------
from backend.agent.harness import (  # noqa: E402
    HarnessConfig,
    HarnessEdit,
    EditDimension,
    EditOperation,
    apply_edit,
    apply_edits,
    revert_edit,
    diff_configs,
    HarnessConfigStore,
)
from backend.agent.harness_defaults import (  # noqa: E402
    get_preset,
    list_presets,
    build_config,
)
from backend.agent.digester import Digester  # noqa: E402
from backend.agent.evolution import (  # noqa: E402
    Planner,
    Critic,
    Evolver,
    EvolutionLoop,
)
from backend.agent.coevolution import (  # noqa: E402
    Trajectory,
    MixedPolicyBuffer,
    GRPOTrainer,
    CoEvolutionLoop,
)


# ---------------------------------------------------------------------------
# Deterministic fakes
# ---------------------------------------------------------------------------

class _StepStatus(SimpleNamespace):
    pass


def _failed_step(index: int, tool: str, error: str) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        tool=tool,
        args={"query": "x"},
        error=error,
        status=SimpleNamespace(value="failed"),
        verification={"config_id": "cfg_x", "confidence": 0.4},
    )


def _ok_step(index: int, tool: str) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        tool=tool,
        args={"query": "x"},
        error=None,
        status=SimpleNamespace(value="completed"),
        verification={"confidence": 0.9},
    )


def mock_run_result(query: str, *, fail: bool = True, run_id: str = "r1",
                     tool: str = "web_search") -> SimpleNamespace:
    """Build an AgentRunResult-shaped object the Digester can consume."""
    if fail:
        steps = [
            _failed_step(0, tool, "Request timeout: timed out after 30s"),
            _failed_step(1, tool, "timeout while waiting for upstream response"),
        ]
    else:
        steps = [_ok_step(0, tool)]
    return SimpleNamespace(run_id=run_id, query=query, plan=SimpleNamespace(steps=steps))


class MockAgentResult:
    def __init__(self, success: bool, duration_ms: float = 4.0):
        self.success = success
        self.duration_ms = duration_ms


class MockAgent:
    """Succeeds iff the config is an evolved variant (evolution_round >= 1).

    This creates a clean improvement signal: the baseline (round 0) config fails,
    every evolved variant succeeds — so the loop observes baseline=0.0 -> best=1.0.
    """

    def __init__(self, cfg: HarnessConfig):
        self.cfg = cfg

    async def run_to_completion(self, query: str) -> MockAgentResult:
        return MockAgentResult(success=self.cfg.evolution_round >= 1)


class HeuristicPlanner(Planner):
    """Deterministic planner bypassing the LLM.

    The mock provider returns non-JSON echo text, so Planner.propose would parse
    zero edits and skip the evolve stage. We force the heuristic path so the full
    digest -> plan -> critic -> evolve pipeline is exercised every run.
    """

    async def propose(self, config, clusters, *, max_edits: int = 5):
        return self._heuristic_edits(config, clusters, max_edits)


class DeterministicDigester(Digester):
    """Digester that never calls the LLM for pattern summarisation."""

    async def digest(self, agent_history, *, use_llm: bool = False):
        return await super().digest(agent_history, use_llm=False)


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"    \033[32m✓\033[0m {msg}")


def _stage(name: str, fn):
    print(f"\n=== Stage: {name} ===")
    try:
        fn()
        print(f"  \033[32mPASS\033[0m — {name}")
        return True
    except Exception:
        print(f"  \033[31mFAIL\033[0m — {name}")
        traceback.print_exc()
        return False


async def _astage(name: str, coro):
    print(f"\n=== Stage: {name} ===")
    try:
        await coro
        print(f"  \033[32mPASS\033[0m — {name}")
        return True
    except Exception:
        print(f"  \033[31mFAIL\033[0m — {name}")
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def stage_1_algebra() -> None:
    base = get_preset("research")
    assert base.config_id, "preset must have a config_id"

    # build_config layering + override
    cfg = build_config(presets=["research"], max_steps=7, temperature=0.15)
    assert cfg.control_flow.max_steps == 7
    assert abs(cfg.llm_routing.temperature - 0.15) < 1e-9
    _ok(f"build_config(presets=[research], max_steps=7, temperature=0.15) -> "
        f"max_steps={cfg.control_flow.max_steps}, temp={cfg.llm_routing.temperature}")

    # apply_edit produces a new config (clone) with incremented version + new id
    edit = HarnessEdit(
        dimension=EditDimension.CONTROL_FLOW,
        field_path="control_flow.max_seconds",
        operation=EditOperation.ADJUST,
        new_value=1.5,
        rationale="increase time budget for slow sources",
        proposed_by="smoke",
        parent_config_id=base.config_id,
    )
    evolved = apply_edit(base, edit)
    assert evolved is not base, "apply_edit must clone, not mutate"
    assert evolved.config_id != base.config_id, "evolved must get a new config_id"
    assert evolved.version == base.version + 1
    assert evolved.parent_id == base.config_id
    assert abs(evolved.control_flow.max_seconds - base.control_flow.max_seconds * 1.5) < 1e-6
    assert abs(base.control_flow.max_seconds - 180.0) < 1e-6, "base must be unmutated"
    _ok(f"apply_edit ADJUST x1.5: max_seconds {base.control_flow.max_seconds} -> "
        f"{evolved.control_flow.max_seconds}, new config_id, version {evolved.version}")

    # diff_configs round-trips the edit (field_path is the top-level dict key,
    # dimension is inferred from that key — e.g. "control_flow" -> CONTROL_FLOW)
    diffs = diff_configs(base, evolved)
    assert diffs, "diff_configs must detect the change"
    cf_diffs = [d for d in diffs if d.dimension == EditDimension.CONTROL_FLOW]
    assert cf_diffs, f"expected a CONTROL_FLOW diff, got {[(d.dimension.value, d.field_path) for d in diffs]}"
    assert cf_diffs[0].field_path == "control_flow"
    _ok(f"diff_configs(base, evolved) -> {len(diffs)} edit(s); "
        f"CONTROL_FLOW field_path={cf_diffs[0].field_path}")

    # revert_edit undoes the change
    reverted = revert_edit(evolved, edit)
    assert abs(reverted.control_flow.max_seconds - base.control_flow.max_seconds) < 1e-6
    _ok(f"revert_edit restores max_seconds to {reverted.control_flow.max_seconds}")

    # apply_edits composes sequentially
    e2 = HarnessEdit(
        dimension=EditDimension.MEMORY,
        field_path="memory_policy.retrieval_k",
        operation=EditOperation.ADJUST,
        new_value=2.0,
        rationale="more context",
        proposed_by="smoke",
        parent_config_id=evolved.config_id,
    )
    combo = apply_edits(base, [edit, e2])
    assert abs(combo.control_flow.max_seconds - 180.0 * 1.5) < 1e-6
    assert combo.memory_policy.retrieval_k == base.memory_policy.retrieval_k * 2
    _ok("apply_edits composes CONTROL_FLOW + MEMORY edits sequentially")

    # HarnessConfigStore round-trip in a temp dir
    with tempfile.TemporaryDirectory() as d:
        store = HarnessConfigStore(base_dir=d)
        cid = store.save(evolved)
        loaded = store.load(cid)
        assert loaded is not None and loaded.config_id == evolved.config_id
        names = store.list_configs()
        assert any(c.config_id == evolved.config_id for c in names)
        _ok(f"HarnessConfigStore save/load/list_configs round-trip ({len(names)} config(s))")


def stage_2_digester() -> None:
    """Validate the mock history is shaped the way Digester expects (sync pre-check)."""
    history = [
        mock_run_result("question A?", fail=True, run_id="r1", tool="web_search"),
        mock_run_result("question B?", fail=True, run_id="r2", tool="web_search"),
        mock_run_result("question C?", fail=True, run_id="r3", tool="scrape_url"),
    ]
    for r in history:
        assert hasattr(r, "plan") and hasattr(r.plan, "steps")
        failed = [s for s in r.plan.steps if s.status.value == "failed" and s.error]
        assert failed, "each mock run must have >=1 failed step"
    _ok(f"history shape OK: {len(history)} runs, all with failed steps "
        f"(category -> timeout -> CONTROL_FLOW)")


async def stage_2_digester_async() -> None:
    digester = DeterministicDigester()
    history = [
        mock_run_result("question A?", fail=True, run_id="r1", tool="web_search"),
        mock_run_result("question B?", fail=True, run_id="r2", tool="web_search"),
        mock_run_result("question C?", fail=True, run_id="r3", tool="scrape_url"),
    ]
    clusters = await digester.digest(history, use_llm=False)
    assert clusters, "digester must produce >=1 cluster from timeout failures"
    # web_search timeout cluster (2 failures) should dominate
    top = clusters[0]
    d = top.to_dict()
    assert d["common_error_category"] == "timeout", d["common_error_category"]
    assert d["suggested_dimension"] == EditDimension.CONTROL_FLOW.value, d["suggested_dimension"]
    assert d["count"] >= 2
    _ok(f"digest() -> {len(clusters)} cluster(s); top: tool={d['common_tool']}, "
        f"cat={d['common_error_category']}, dim={d['suggested_dimension']}, count={d['count']}")


async def stage_3_plan_critic_evolve_async() -> None:
    base = get_preset("research")
    digester = DeterministicDigester()
    history = [mock_run_result("q?", fail=True, run_id="r1", tool="web_search"),
               mock_run_result("q2?", fail=True, run_id="r2", tool="web_search")]
    clusters = await digester.digest(history, use_llm=False)

    planner = HeuristicPlanner()
    edits = await planner.propose(base, clusters)
    assert edits, "heuristic planner must propose >=1 edit for timeout clusters"
    _ok(f"Planner.propose -> {len(edits)} edit(s): "
        f"{[e.dimension.value + ':' + e.field_path for e in edits]}")

    critic = Critic(acceptance_threshold=0.4)  # mock LLM fails JSON parse -> default accept@0.5
    accepted = await critic.filter_edits(edits, clusters, base)
    assert accepted, "critic must accept >=1 edit at threshold 0.4"
    _ok(f"Critic.filter_edits -> {len(accepted)}/{len(edits)} accepted "
        f"(confidence={accepted[0].critic_confidence})")

    evolver = Evolver(store_results=False)
    variant = evolver.evolve(base, accepted)
    assert variant.config_id != base.config_id
    assert variant.evolution_round == base.evolution_round + 1
    _ok(f"Evolver.evolve -> variant (round {variant.evolution_round}, "
        f"max_seconds={variant.control_flow.max_seconds})")


async def stage_4_evolution_loop() -> None:
    base = get_preset("research")
    tasks = [SimpleNamespace(prompt=f"question {i}?") for i in range(3)]

    def factory(cfg: HarnessConfig) -> MockAgent:
        return MockAgent(cfg)

    loop = EvolutionLoop(
        base, tasks, factory,
        max_rounds=3,
        variants_per_round=1,
        planner=HeuristicPlanner(),
        critic=Critic(acceptance_threshold=0.4),
        digester=DeterministicDigester(),
    )
    # Seed history with timeout failures so round 1 has clusters to digest.
    loop.append_history([mock_run_result(t.prompt, fail=True, run_id=f"r{i}")
                         for i, t in enumerate(tasks)])

    result = await loop.run()

    assert result.baseline_score == 0.0, f"baseline should fail (got {result.baseline_score})"
    assert len(result.rounds) >= 2, f"need baseline + >=1 evolution round (got {len(result.rounds)})"
    assert result.total_edits >= 1, "at least one edit must be applied across rounds"
    assert result.best_score == 1.0, f"evolved variant should succeed (got {result.best_score})"
    assert result.improvement == 1.0
    assert result.best_config is not None
    assert result.best_config.evolution_round >= 1
    _ok(f"EvolutionLoop.run: baseline={result.baseline_score} -> best={result.best_score} "
        f"({len(result.rounds)} rounds, {result.total_edits} edits, "
        f"+{result.improvement*100:.0f}% in {result.total_duration_seconds:.1f}s)")


async def stage_5_buffer_grpo() -> None:
    cfg_a = get_preset("research")
    cfg_b = apply_edit(cfg_a, HarnessEdit(
        dimension=EditDimension.CONTROL_FLOW,
        field_path="control_flow.max_seconds",
        operation=EditOperation.ADJUST,
        new_value=1.5,
        rationale="evolved",
        proposed_by="smoke",
        parent_config_id=cfg_a.config_id,
    ))
    assert cfg_a.config_id != cfg_b.config_id

    # Permissive buffer (delta threshold 1.0) -> cross-config trajectories survive the filter.
    buffer = MixedPolicyBuffer(config_delta_threshold=1.0)
    buffer.add(Trajectory(query="q1", config_id=cfg_a.config_id, success=False, score=0.0))
    buffer.add(Trajectory(query="q1", config_id=cfg_b.config_id, success=True,  score=1.0))
    assert buffer._total == 2

    queries = buffer.get_all_queries()  # the query-key hashing bug fix: returns original text
    assert queries == ["q1"], f"get_all_queries must return original text, got {queries}"
    _ok(f"get_all_queries() returns original text (bug-fix verified): {queries}")

    trajs = buffer.get_for_query("q1", current_config=cfg_a)
    assert len(trajs) == 2, "permissive threshold must keep both variants"
    _ok(f"get_for_query (threshold=1.0) keeps both variants: {len(trajs)} trajectories")

    trainer = GRPOTrainer(buffer, min_pairs_per_query=2, min_advantage=0.1)
    examples = trainer.prepare_examples(current_config=cfg_a)
    assert examples, "must produce >=1 GRPO example from the success/failure pair"
    ex = examples[0]
    assert ex.chosen_config_id == cfg_b.config_id, "chosen must be the succeeding config"
    assert ex.rejected_config_id == cfg_a.config_id, "rejected must be the failing config"
    assert abs(ex.advantage - 1.0) < 1e-6, f"advantage should be 1.0, got {ex.advantage}"
    _ok(f"GRPO.prepare_examples -> {len(examples)} pair(s); chosen=B(reward=1.0) "
        f"vs rejected=A(reward=0.0), advantage={ex.advantage}")

    train_res = await trainer.train(current_config=cfg_a, dry_run=True)
    assert train_res["examples_count"] >= 1
    assert "mean_advantage" in train_res
    assert trainer._training_rounds == 1
    _ok(f"GRPO.train(dry_run) -> examples={train_res['examples_count']}, "
        f"mean_adv={train_res['mean_advantage']:.3f}, rounds={trainer._training_rounds}")

    # Bounded off-policy bias (§5.4): default threshold 0.5 prunes distant configs.
    strict = MixedPolicyBuffer()  # config_delta_threshold=0.5
    strict.add(Trajectory(query="q1", config_id=cfg_a.config_id, success=False, score=0.0))
    strict.add(Trajectory(query="q1", config_id=cfg_b.config_id, success=True,  score=1.0))
    kept = strict.get_for_query("q1", current_config=cfg_a)
    # cfg_b's hash prefix differs from cfg_a in ~all of 8 hex chars -> distance ~0.94 > 0.5
    assert len(kept) <= 1, "default threshold should prune the distant variant"
    _ok(f"bounded off-policy bias: default threshold=0.5 prunes distant variant "
        f"({len(kept)} of 2 kept)")


async def stage_6_coevolution() -> None:
    base = get_preset("research")
    tasks = [SimpleNamespace(prompt=f"question {i}?") for i in range(3)]

    def factory(cfg: HarnessConfig) -> MockAgent:
        return MockAgent(cfg)

    def evo_factory(cfg: HarnessConfig) -> EvolutionLoop:
        loop = EvolutionLoop(
            cfg, tasks, factory,
            max_rounds=2,
            variants_per_round=1,
            planner=HeuristicPlanner(),
            critic=Critic(acceptance_threshold=0.4),
            digester=DeterministicDigester(),
        )
        loop.append_history([mock_run_result(t.prompt, fail=True, run_id=f"r{i}")
                             for i, t in enumerate(tasks)])
        return loop

    coev = CoEvolutionLoop(
        base, tasks, factory,
        co_evolution_cycles=2,
        harness_rounds_per_cycle=2,
        evolution_loop_factory=evo_factory,
    )
    result = await coev.run()

    assert len(result.cycles) >= 1, "at least one co-evolution cycle must run"
    assert result.baseline_score == 0.0
    assert result.final_score == 1.0
    assert result.total_improvement == 1.0
    assert result.total_buffer_trajectories > 0, "buffer must collect trajectories"
    assert result.total_training_rounds >= 1, "GRPO trainer must run >=1 round"
    assert result.final_config is not None
    c0 = result.cycles[0]
    _ok(f"Cycle 0: harness_rounds={c0.harness_rounds}, "
        f"harness_best={c0.harness_best_score}, train_examples={c0.training_examples}, "
        f"buffer={c0.buffer_size}, model_trained={c0.model_trained}")
    _ok(f"CoEvolutionLoop.run: baseline={result.baseline_score} -> final={result.final_score} "
        f"({len(result.cycles)} cycles, buffer={result.total_buffer_trajectories}, "
        f"train_rounds={result.total_training_rounds}, +{result.total_improvement*100:.0f}% "
        f"in {result.total_duration_seconds:.1f}s)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_all() -> int:
    print("=" * 64)
    print("HarnessX Phase 3+4 end-to-end smoke test")
    print(f"presets available: {sorted(list_presets().keys())}")
    print("=" * 64)

    results = []
    results.append(_stage("1. Substitution algebra", stage_1_algebra))
    results.append(_stage("2. Digester history shape", stage_2_digester))
    results.append(await _astage("2. Digester async digest", stage_2_digester_async()))
    results.append(await _astage("3. Plan/Critic/Evolve async", stage_3_plan_critic_evolve_async()))
    results.append(await _astage("4. EvolutionLoop end-to-end", stage_4_evolution_loop()))
    results.append(await _astage("5. MixedPolicyBuffer + GRPOTrainer", stage_5_buffer_grpo()))
    results.append(await _astage("6. CoEvolutionLoop end-to-end", stage_6_coevolution()))

    passed = sum(results)
    total = len(results)
    print("\n" + "=" * 64)
    print(f"RESULT: {passed}/{total} stages passed")
    print("=" * 64)
    return 0 if passed == total else 1


def main() -> int:
    return asyncio.run(run_all())


if __name__ == "__main__":
    sys.exit(main())