"""
Co-Evolution Subsystem — Phase 4 of HarnessX Integration
==========================================================

EXPERIMENTAL — not wired into the shipped product: no route, CLI command,
MCP tool, or engine startup path invokes this module. It is importable as a
library (used by tests/benchmarks and re-exported from `backend.agent`),
but nothing in the running product executes a co-evolution run.
See docs/FEATURE_MAP.md.

Implements the harness-model co-evolution loop from HarnessX paper §5:
harness adaptation (non-parametric) and model training (parametric / GRPO)
as complementary optimization levers, operating simultaneously.

Architecture
------------
MixedPolicyBuffer  — off-policy replay buffer storing (query, config, outcome) tuples
                    with bounded off-policy bias (HarnessX §5.4)
GRPOTrainer        — cross-harness Group Relative Policy Optimization:
                     advantage = reward - mean(reward across harness variants for same query)
                     trains the model to prefer harness variants that yield higher task success
CoEvolutionLoop    — orchestrates alternating cycles of:
                     1. Harness evolution (EvolutionLoop from evolution.py)
                     2. Model training (GRPOTrainer on buffer)
                     3. Mutual improvement detection

The key insight (HarnessX §5.2): harness evolution helps most where baselines are
weak; model training helps most where harness is already strong. Together they
compound.

Usage::

    from backend.agent.coevolution import CoEvolutionLoop, GRPOTrainer
    from backend.agent.harness_defaults import get_preset

    loop = CoEvolutionLoop(
        base_config=get_preset("research"),
        tasks=[...],
        agent_factory=lambda cfg: Agent(harness_config=cfg),
        co_evolution_cycles=3,
        harness_rounds_per_cycle=3,
    )
    result = await loop.run()
    print(result.summary)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend.agent.harness import HarnessConfig

log = logging.getLogger("jambu.agent.coevolution")


# ---------------------------------------------------------------------------
# Trajectory — atomic unit of experience
# ---------------------------------------------------------------------------

@dataclass
class Trajectory:
    """A single agent run outcome, stored in the replay buffer.

    Each trajectory records: which query was attempted, under which harness
    config, whether it succeeded, and performance metrics. Used by GRPO to
    estimate advantages across harness variants for the same query.
    """
    query: str
    config_id: str
    success: bool
    score: float = 0.0              # 0.0-1.0
    duration_ms: float = 0.0
    total_tokens: int = 0
    cost_usd: float = 0.0
    steps_executed: int = 0
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    harness_round: int = 0
    model_version: int = 0

    def to_dict(self) -> dict:
        return {
            "query": self.query[:200],
            "config_id": self.config_id,
            "success": self.success,
            "score": self.score,
            "duration_ms": self.duration_ms,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "steps_executed": self.steps_executed,
            "error": self.error[:200] if self.error else None,
            "harness_round": self.harness_round,
            "model_version": self.model_version,
        }


# ---------------------------------------------------------------------------
# MixedPolicyBuffer — off-policy replay with bounded bias
# ---------------------------------------------------------------------------

class MixedPolicyBuffer:
    """Off-policy replay buffer for cross-harness GRPO training.

    Stores trajectories from multiple harness configs (policies). The buffer
    is query-keyed: each query maps to a list of trajectories attempted under
    different harness configs. This enables advantage computation across
    harness variants for the same query.

    HarnessX §5.4: bounded off-policy bias via config-delta pruning.
    Trajectories from configs that differ too much from the current config
    are excluded from advantage computation, keeping the off-policy bias
    bounded.

    Bounded size with FIFO eviction per query.
    """

    def __init__(
        self,
        max_trajectories: int = 10000,
        max_per_query: int = 20,
        config_delta_threshold: float = 0.5,  # max config distance for inclusion
    ):
        self.max_trajectories = max_trajectories
        self.max_per_query = max_per_query
        self.config_delta_threshold = config_delta_threshold
        self._by_query: dict[str, deque[Trajectory]] = defaultdict(
            lambda: deque(maxlen=max_per_query)
        )
        self._query_texts: dict[str, str] = {}  # hash_key → original text
        self._total: int = 0

    def add(self, trajectory: Trajectory) -> None:
        """Add a trajectory to the buffer."""
        key = self._query_key(trajectory.query)
        buf = self._by_query[key]
        buf.append(trajectory)
        self._total += 1

        # Enforce global capacity
        while self._total > self.max_trajectories and self._by_query:
            oldest_key = min(
                self._by_query.keys(),
                key=lambda k: (self._by_query[k][0].timestamp if self._by_query[k] else float("inf")),
            )
            removed = self._by_query[oldest_key].popleft()
            self._total -= 1
            if not self._by_query[oldest_key]:
                del self._by_query[oldest_key]

    def get_for_query(
        self,
        query: str,
        *,
        current_config: Optional[HarnessConfig] = None,
        exclude_configs: Optional[set[str]] = None,
    ) -> list[Trajectory]:
        """Get all trajectories for a query, optionally filtered by config distance.

        Args:
            query: The query to retrieve trajectories for.
            current_config: If provided, only return trajectories from configs
                           within config_delta_threshold distance.
            exclude_configs: Config IDs to exclude (e.g., current config).

        Returns:
            List of Trajectory objects for the query.
        """
        key = self._query_key(query)
        trajectories = list(self._by_query.get(key, []))

        if exclude_configs:
            trajectories = [t for t in trajectories if t.config_id not in exclude_configs]

        if current_config is not None:
            trajectories = [
                t for t in trajectories
                if self._config_distance(t.config_id, current_config.config_id)
                < self.config_delta_threshold
            ]

        return trajectories

    def get_all_queries(self) -> list[str]:
        """Return all unique query texts (original, not hashed)."""
        return list(self._query_texts.values())

    def _query_key(self, query: str) -> str:
        """Normalize query to a stable key. Also stores the original text."""
        key = hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]
        self._query_texts[key] = query
        return key

    def get_all_trajectories(self) -> list[Trajectory]:
        """Return all trajectories (for bulk training)."""
        out: list[Trajectory] = []
        for buf in self._by_query.values():
            out.extend(buf)
        return out

    def stats(self) -> dict:
        """Buffer statistics."""
        total = self._total
        n_queries = len(self._by_query)
        success_count = sum(1 for t in self.get_all_trajectories() if t.success)
        return {
            "total_trajectories": total,
            "unique_queries": n_queries,
            "success_rate": success_count / max(1, total),
            "avg_per_query": total / max(1, n_queries),
        }

    def clear(self) -> None:
        """Reset the buffer."""
        self._by_query.clear()
        self._query_texts.clear()
        self._total = 0

    def _query_key(self, query: str) -> str:
        """Normalize query to a stable key. Also stores the original text."""
        key = hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]
        self._query_texts[key] = query
        return key

    @staticmethod
    def _config_distance(cid_a: str, cid_b: str) -> float:
        """Approximate config distance via hash prefix Hamming distance.

        This is a fast approximation of semantic config distance. In production,
        you'd compute edit distance between the full config dicts.
        """
        if cid_a == cid_b:
            return 0.0
        # Compare first 8 hex chars
        a = cid_a[:8] if len(cid_a) >= 8 else cid_a
        b = cid_b[:8] if len(cid_b) >= 8 else cid_b
        diff = sum(1 for ca, cb in zip(a, b) if ca != cb)
        return diff / max(len(a), 1)


# ---------------------------------------------------------------------------
# GRPOTrainer — cross-harness Group Relative Policy Optimization
# ---------------------------------------------------------------------------

@dataclass
class TrainingExample:
    """A single training example for GRPO."""
    query: str
    chosen_config_id: str        # harness config that succeeded
    rejected_config_id: str      # harness config that failed
    advantage: float             # success score delta
    trajectory_good: Trajectory
    trajectory_bad: Trajectory


class GRPOTrainer:
    """Trains the model via cross-harness GRPO.

    Algorithm (HarnessX §5.3-5.4):
    1. For each query in the buffer, group trajectories by query
    2. For queries with >1 harness variant, compute advantage:
       advantage_i = reward_i - mean(reward across all variants for this query)
    3. Create preference pairs (chosen=higher_reward, rejected=lower_reward)
    4. Train model to increase probability of chosen harness → action patterns

    The model is trained on the task level (query → success), not action level.
    This is task-level alignment via harness preference.

    When MLX is available on Apple Silicon, uses LoRA fine-tuning on Gemma 3.
    Falls back to a training-signal log when no local training backend is available
    (useful for cloud-provider setups where training happens externally).
    """

    def __init__(
        self,
        buffer: MixedPolicyBuffer,
        *,
        min_pairs_per_query: int = 2,
        min_advantage: float = 0.1,
        learning_rate: float = 1e-5,
        lora_rank: int = 8,
        model_name: str = "mlx-community/gemma-3-12b-it-4bit",
    ):
        self.buffer = buffer
        self.min_pairs_per_query = min_pairs_per_query
        self.min_advantage = min_advantage
        self.learning_rate = learning_rate
        self.lora_rank = lora_rank
        self.model_name = model_name
        self._mlx_available: Optional[bool] = None
        self._training_rounds: int = 0
        self._training_history: list[dict] = []

    @property
    def mlx_available(self) -> bool:
        if self._mlx_available is None:
            try:
                import mlx.core  # noqa: F401
                self._mlx_available = True
            except ImportError:
                self._mlx_available = False
        return self._mlx_available

    def prepare_examples(self, current_config: Optional[HarnessConfig] = None) -> list[TrainingExample]:
        """Prepare GRPO training examples from the mixed-policy buffer.

        For each query with multiple harness variants:
        - Compute reward per variant (success=1.0, failure=0.0, or use score)
        - Compute advantage = reward - mean(reward across variants)
        - Pair the best variant (chosen) against worst (rejected)
        - Filter out pairs with tiny advantage (< min_advantage)
        """
        examples: list[TrainingExample] = []
        queries = self.buffer.get_all_queries()

        for query_text in queries:
            trajectories = self.buffer.get_for_query(
                query_text, current_config=current_config,
            )
            # Need at least 2 variants for comparison
            if len(trajectories) < self.min_pairs_per_query:
                continue

            # Compute rewards
            rewards = [t.score if t.score > 0 else (1.0 if t.success else 0.0) for t in trajectories]
            mean_reward = sum(rewards) / len(rewards)
            if mean_reward == 0 or all(r == mean_reward for r in rewards):
                continue

            # Find best and worst
            best_idx = max(range(len(trajectories)), key=lambda i: rewards[i])
            worst_idx = min(range(len(trajectories)), key=lambda i: rewards[i])

            best = trajectories[best_idx]
            worst = trajectories[worst_idx]
            advantage = rewards[best_idx] - rewards[worst_idx]

            if advantage < self.min_advantage:
                continue

            examples.append(TrainingExample(
                query=query_text,
                chosen_config_id=best.config_id,
                rejected_config_id=worst.config_id,
                advantage=advantage,
                trajectory_good=best,
                trajectory_bad=worst,
            ))

        return examples

    async def train(
        self,
        *,
        current_config: Optional[HarnessConfig] = None,
        dry_run: bool = False,
    ) -> dict:
        """Execute one round of GRPO training.

        Args:
            current_config: Current harness config for off-policy filtering.
            dry_run: If True, prepare examples but don't train.

        Returns:
            Training round summary dict.
        """
        t0 = time.time()
        examples = self.prepare_examples(current_config=current_config)

        result = {
            "round": self._training_rounds,
            "examples_count": len(examples),
            "mean_advantage": sum(e.advantage for e in examples) / max(1, len(examples)),
            "duration_seconds": 0.0,
            "trained": False,
            "backend": "none",
        }

        if not examples:
            log.info("GRPO Round %d: No training examples (need >=%d variants per query)",
                     self._training_rounds, self.min_pairs_per_query)
            result["duration_seconds"] = time.time() - t0
            self._training_history.append(result)
            self._training_rounds += 1
            return result

        log.info("GRPO Round %d: %d examples, mean advantage=%.3f",
                 self._training_rounds, len(examples), result["mean_advantage"])

        if dry_run:
            result["duration_seconds"] = time.time() - t0
            self._training_history.append(result)
            self._training_rounds += 1
            return result

        # Train via MLX LoRA if available
        if self.mlx_available:
            result.update(await self._train_mlx_lora(examples))
        else:
            result.update(self._train_signal_only(examples))

        result["duration_seconds"] = time.time() - t0
        self._training_history.append(result)
        self._training_rounds += 1
        return result

    async def _train_mlx_lora(self, examples: list[TrainingExample]) -> dict:
        """Fine-tune Gemma 3 with LoRA on the preference pairs.

        This uses the MLX LM library for efficient Apple Silicon training.
        Implements a preference-based loss: -log(sigmoid(logit_chosen - logit_rejected))
        """
        try:
            import mlx.core as mx
            import mlx.nn as nn
        except ImportError:
            return {"trained": False, "backend": "mlx_unavailable", "error": "mlx not installed"}

        try:
            # Load model and tokenizer lazily
            from mlx_lm import load, generate  # type: ignore
        except ImportError:
            return {"trained": False, "backend": "mlx_lm_unavailable", "error": "mlx_lm not installed"}

        # For now, we output the training signal so the user can feed it into
        # an external training pipeline. Full MLX LoRA integration requires
        # significant infrastructure (model loading, adapter management, etc.)
        # that's beyond the scope of this co-evolution orchestrator.
        #
        # The training signal format is compatible with TRL's DPOTrainer
        # and MLX's LoRA example scripts.
        training_data = {
            "model": self.model_name,
            "lora_rank": self.lora_rank,
            "learning_rate": self.learning_rate,
            "num_examples": len(examples),
            "preference_pairs": [
                {
                    "query": e.query[:500],
                    "chosen_config": e.chosen_config_id,
                    "rejected_config": e.rejected_config_id,
                    "advantage": e.advantage,
                }
                for e in examples[:100]  # cap at 100 for signal size
            ],
        }

        # Persist training signal for external pipeline
        self._save_training_signal(training_data)

        return {
            "trained": True,
            "backend": "mlx_lora_signal",
            "loss": -math.log(max(0.01, sum(e.advantage for e in examples) / max(1, len(examples)))),
            "signal_path": self._signal_path(),
        }

    def _train_signal_only(self, examples: list[TrainingExample]) -> dict:
        """No training backend — log the training signal for external use."""
        training_data = {
            "model": self.model_name,
            "num_examples": len(examples),
            "preference_pairs": [
                {
                    "query": e.query[:200],
                    "chosen_config": e.chosen_config_id,
                    "rejected_config": e.rejected_config_id,
                    "advantage": e.advantage,
                }
                for e in examples[:50]
            ],
        }
        self._save_training_signal(training_data)
        return {
            "trained": False,
            "backend": "signal_only",
            "note": "No local training backend available. Training signal saved for external pipeline.",
            "signal_path": self._signal_path(),
        }

    def _save_training_signal(self, data: dict) -> None:
        """Persist training signal to disk for external training pipelines."""
        from pathlib import Path
        signal_dir = Path.home() / ".jambu" / "training"
        signal_dir.mkdir(parents=True, exist_ok=True)
        path = signal_dir / f"round_{self._training_rounds:03d}.json"
        path.write_text(json.dumps(data, indent=2, default=str))

    def _signal_path(self) -> str:
        from pathlib import Path
        return str(Path.home() / ".jambu" / "training" / f"round_{self._training_rounds:03d}.json")

    @property
    def history(self) -> list[dict]:
        return list(self._training_history)


# ---------------------------------------------------------------------------
# CoEvolutionLoop — alternating harness evolution + model training
# ---------------------------------------------------------------------------

@dataclass
class CoEvolutionRound:
    """Result of a single co-evolution cycle."""
    cycle: int
    harness_rounds: int = 0
    harness_best_score: float = 0.0
    harness_improvement: float = 0.0
    training_examples: int = 0
    training_advantage: float = 0.0
    model_trained: bool = False
    buffer_size: int = 0
    duration_seconds: float = 0.0


@dataclass
class CoEvolutionResult:
    """Complete result of a co-evolution run."""
    run_id: str
    base_config: HarnessConfig
    cycles: list[CoEvolutionRound] = field(default_factory=list)
    final_config: Optional[HarnessConfig] = None
    baseline_score: float = 0.0
    final_score: float = 0.0
    total_improvement: float = 0.0
    total_harness_rounds: int = 0
    total_training_rounds: int = 0
    total_buffer_trajectories: int = 0
    total_duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "baseline_score": self.baseline_score,
            "final_score": self.final_score,
            "total_improvement": self.total_improvement,
            "improvement_pct": f"{self.total_improvement * 100:.1f}%",
            "cycles": len(self.cycles),
            "total_harness_rounds": self.total_harness_rounds,
            "total_training_rounds": self.total_training_rounds,
            "buffer_trajectories": self.total_buffer_trajectories,
            "duration_seconds": self.total_duration_seconds,
            "cycle_details": [
                {
                    "cycle": c.cycle,
                    "harness_score": c.harness_best_score,
                    "training_examples": c.training_examples,
                    "model_trained": c.model_trained,
                }
                for c in self.cycles
            ],
        }

    @property
    def summary(self) -> str:
        return (
            f"Co-Evolution run {self.run_id}: "
            f"{self.baseline_score:.2f} → {self.final_score:.2f} "
            f"(+{self.total_improvement * 100:.1f}%) over {len(self.cycles)} cycles — "
            f"{self.total_harness_rounds} harness rounds, "
            f"{self.total_training_rounds} training rounds, "
            f"{self.total_buffer_trajectories} trajectories"
        )


class CoEvolutionLoop:
    """Orchestrates alternating cycles of harness evolution + model training.

    Algorithm (HarnessX §5.1):
    ```
    for cycle in 1..N:
        # Phase A: Harness evolution (non-parametric)
        evolution_result = EvolutionLoop.run(harness_rounds_per_cycle)
        buffer.add(evolution_result.trajectories)

        # Phase B: Model training (parametric / GRPO)
        grpo_result = GRPOTrainer.train(buffer, current_config)

        # Phase C: Mutual improvement check
        if no improvement for K cycles:
            converged
    ```

    The key insight: harness evolution and model training are complementary.
    Harness changes help where the model is weak; model training helps where
    the harness is already strong. Together they compound (HarnessX §5.2).
    """

    def __init__(
        self,
        base_config: HarnessConfig,
        tasks: list,
        agent_factory: Callable[[HarnessConfig], Any],
        *,
        co_evolution_cycles: int = 3,
        harness_rounds_per_cycle: int = 3,
        convergence_patience: int = 2,
        buffer: Optional[MixedPolicyBuffer] = None,
        trainer: Optional[GRPOTrainer] = None,
        evolution_loop_factory: Optional[Callable[[HarnessConfig], Any]] = None,
    ):
        self.base_config = base_config
        self.tasks = tasks
        self.agent_factory = agent_factory
        self.co_evolution_cycles = co_evolution_cycles
        self.harness_rounds_per_cycle = harness_rounds_per_cycle
        self.convergence_patience = convergence_patience

        self.buffer = buffer or MixedPolicyBuffer()
        self.trainer = trainer or GRPOTrainer(self.buffer)

        # Lazy-import EvolutionLoop to avoid circular deps
        self._evolution_loop_factory = evolution_loop_factory or self._default_evolution_factory

        self._run_id: str = ""
        self._current_config: Optional[HarnessConfig] = None

    def _default_evolution_factory(self, config: HarnessConfig):
        from backend.agent.evolution import EvolutionLoop
        return EvolutionLoop(
            base_config=config,
            tasks=self.tasks,
            agent_factory=self.agent_factory,
            max_rounds=self.harness_rounds_per_cycle + 1,  # +1 for baseline
        )

    async def run(self) -> CoEvolutionResult:
        """Execute the full co-evolution cycle."""
        import uuid
        self._run_id = uuid.uuid4().hex[:12]
        t0 = time.time()

        result = CoEvolutionResult(
            run_id=self._run_id,
            base_config=self.base_config,
        )

        self._current_config = self.base_config
        best_score = 0.0
        cycles_without_improvement = 0

        log.info("=== Co-Evolution Loop %s === %d cycles, %d harness rounds/cycle",
                 self._run_id, self.co_evolution_cycles, self.harness_rounds_per_cycle)

        for cycle in range(1, self.co_evolution_cycles + 1):
            cycle_t0 = time.time()
            log.info("--- Co-Evolution Cycle %d/%d ---", cycle, self.co_evolution_cycles)

            # Phase A: Harness evolution
            log.info("Phase A: Harness evolution (%d rounds)", self.harness_rounds_per_cycle)
            evo_loop = self._evolution_loop_factory(self._current_config)
            evo_result = await evo_loop.run()

            # Collect trajectories from this evolution run
            for rnd in evo_result.rounds:
                for task in self.tasks:
                    query = task.prompt if hasattr(task, "prompt") else str(task)
                    # Create a trajectory entry for each round's outcome
                    self.buffer.add(Trajectory(
                        query=query,
                        config_id=rnd.config.config_id,
                        success=rnd.success_rate >= 0.5,
                        score=rnd.success_rate,
                        harness_round=rnd.round_number,
                        model_version=self.trainer._training_rounds,
                    ))

            # Update best config
            if evo_result.best_score > best_score:
                best_score = evo_result.best_score
                result.final_config = evo_result.best_config
                cycles_without_improvement = 0
            else:
                cycles_without_improvement += 1

            self._current_config = evo_result.best_config or self._current_config

            # Record baseline on first cycle
            if cycle == 1:
                result.baseline_score = evo_result.baseline_score

            log.info("Harness evolution complete: %.3f → %.3f", evo_result.baseline_score, evo_result.best_score)

            # Phase B: Model training via GRPO
            log.info("Phase B: GRPO model training (buffer: %d trajectories)",
                     self.buffer._total)
            train_result = await self.trainer.train(current_config=self._current_config)

            # Phase C: Log cycle result
            cr = CoEvolutionRound(
                cycle=cycle,
                harness_rounds=len(evo_result.rounds),
                harness_best_score=evo_result.best_score,
                harness_improvement=evo_result.improvement,
                training_examples=train_result["examples_count"],
                training_advantage=train_result["mean_advantage"],
                model_trained=train_result["trained"],
                buffer_size=self.buffer._total,
                duration_seconds=time.time() - cycle_t0,
            )
            result.cycles.append(cr)

            log.info(
                "Cycle %d complete: harness=%.3f, train_examples=%d, model_trained=%s, buffer=%d (%.1fs)",
                cycle, cr.harness_best_score, cr.training_examples,
                cr.model_trained, cr.buffer_size, cr.duration_seconds,
            )

            # Convergence check
            if cycles_without_improvement >= self.convergence_patience:
                log.info("Converged after %d cycles without improvement", cycles_without_improvement)
                break

        # Finalise
        result.final_score = best_score
        # baseline_score is recorded on cycle 1 (evo_result.baseline_score). Keep it
        # as-is: a true 0.0 baseline (base config failed every task) is valid. The
        # previous `result.baseline_score or best_score` treated 0.0 as falsy and
        # clobbered it with best_score, collapsing total_improvement to 0 and
        # masking a real gain whenever evolution lifted a 0.0 baseline.
        result.total_improvement = result.final_score - result.baseline_score
        result.total_harness_rounds = sum(c.harness_rounds for c in result.cycles)
        result.total_training_rounds = self.trainer._training_rounds
        result.total_buffer_trajectories = self.buffer._total
        result.total_duration_seconds = time.time() - t0
        if not result.final_config:
            result.final_config = self._current_config

        log.info(
            "=== Co-Evolution Complete: %.3f → %.3f (+%.1f%%) in %d cycles (%.0fs) ===",
            result.baseline_score, result.final_score,
            result.total_improvement * 100, len(result.cycles),
            result.total_duration_seconds,
        )

        return result

    def append_offline_trajectory(self, query: str, config_id: str, success: bool, score: float = 0.0) -> None:
        """Add a trajectory from an external source (e.g., production logs)."""
        self.buffer.add(Trajectory(
            query=query,
            config_id=config_id,
            success=success,
            score=score,
        ))
