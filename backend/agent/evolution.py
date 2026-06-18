"""
AEGIS Evolution Pipeline — Phases 2-3 of HarnessX Integration
===============================================================

Implements the trace-driven multi-agent harness evolution engine described in
the HarnessX paper (§4.3-4.5). The pipeline is:

    Traces → Digester (failure_clusters)
           → Planner (proposed HarnessEdits)
           → Critic (filter/accepted edits)
           → Evolver (apply edits → new config variants)
           → EnsembleRunner (run N variants in parallel)
           → Select best → repeat

Components:
- **Planner** — LLM that proposes concrete HarnessEdits from failure clusters
- **Evolver** — applies edits via the substitution algebra (harness.apply_edits)
- **Critic** — counterfactual evaluation gates edits before deployment
- **EnsembleRunner** — runs N config variants in parallel, compares results
- **EvolutionLoop** — orchestrates the full AEGIS cycle over multiple rounds
- **EvolutionResult** — structured output of an evolution run
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend.agent.harness import (
    HarnessConfig,
    HarnessEdit,
    EditDimension,
    EditOperation,
    apply_edits,
    apply_edit,
    get_config_store,
)
from backend.llm import ChatMessage, Role, Usage, get_default, normalize_llm_response

log = logging.getLogger("jambu.agent.evolution")


# ---------------------------------------------------------------------------
# JSON-safe LLM call helper — wraps a chat call with one retry on parse fail
# ---------------------------------------------------------------------------

async def _call_llm_json(
    messages: list,
    *,
    temperature: float = 0.2,
    max_tokens: int = 800,
    retry_hint: str = "Respond with ONLY valid JSON. No prose, no markdown fencing, no thinking blocks.",
    max_retries: int = 1,
):
    """Call the LLM and return the parsed JSON.

    On JSONDecodeError, re-call once with a hint appended to the user message
    asking for valid JSON. If the second call also fails, raises the original
    exception so the caller can fall back to a heuristic / default-accept.

    Args:
        messages: list of ChatMessage
        temperature, max_tokens: forwarded to llm.chat
        retry_hint: appended to the last user message on retry
        max_retries: 1 by default (single retry before raising)
    """
    import json as _json

    llm = get_default()
    last_err: Exception | None = None
    msgs = list(messages)
    last_resp = None  # for diagnostics on retry
    for attempt in range(max_retries + 1):
        try:
            resp = await llm.chat(msgs, temperature=temperature, max_tokens=max_tokens)
            last_resp = resp
            content = normalize_llm_response(resp.content)
            return _json.loads(content), resp
        except Exception as e:  # noqa: BLE001
            last_err = e
            # If the response was truncated by max_tokens (finish_reason='length')
            # and the after-think content is empty, the retry needs a stronger
            # hint: ask the model to drop its thinking and emit JSON only.
            truncated = (
                last_resp is not None
                and getattr(last_resp, "finish_reason", None) == "length"
            )
            log.warning(
                "LLM JSON call failed (attempt %d/%d, truncated=%s): %s",
                attempt + 1, max_retries + 1, truncated, e,
            )
            if attempt >= max_retries:
                break
            # Compose a more aggressive hint when the response was truncated
            # (the model burned all tokens on a think block and never emitted
            # the actual JSON).
            if truncated:
                hint = (
                    "Your previous response was cut off — you spent the token "
                    "budget on a thinking block and never produced the JSON. "
                    "Skip the thinking this time. Reply with ONLY the JSON "
                    "object, no prose, no markdown, no tags."
                )
            else:
                hint = retry_hint
            if msgs and msgs[-1].role == Role.USER:
                msgs[-1] = ChatMessage(role=Role.USER, content=msgs[-1].content + "\n\n" + hint)
            else:
                msgs = msgs + [ChatMessage(role=Role.USER, content=finish_reason + "\n\n" + hint)]
    # All retries exhausted
    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# Planner — LLM proposes harness edits from failure clusters
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM = """You are a harness evolution planner (AEGIS Planner). Your job is to analyse failure clusters from agent runs and propose concrete, targeted edits to the agent harness that will fix the failures.

Be conservative: prefer small, reversible changes over large rewrites. Each edit must target a specific field in the harness configuration."""

_PLANNER_USER = """CURRENT HARNESS CONFIG:
{config_summary}

FAILURE CLUSTERS (sorted by failure count, most severe first):
{clusters_text}

AVAILABLE EDIT DIMENSIONS AND OPERATIONS:
- prompt: REPLACE — rewrite a prompt template (field_path examples: prompts.planner_user_template, prompts.verifier_user_template, prompts.replanner_user_template, prompts.synthesis_user_template, prompts.task_agent_system)
- tool: APPEND/REMOVE — add or remove tools from the registry (field_path: tool_registry_names)
- memory: ADJUST — change numeric memory policy fields (field_path examples: memory_policy.vector_weight, memory_policy.retrieval_k, memory_policy.recency_tau_days)
- memory: REPLACE — change memory policy boolean fields (field_path examples: memory_policy.use_semantic_memory, memory_policy.use_procedural_memory)
- control_flow: SET — change control flow parameters (field_path examples: control_flow.max_steps, control_flow.max_tokens, control_flow.max_seconds, control_flow.replan_on_failure, control_flow.replan_confidence_threshold)
- llm_routing: SET — change provider/model/routing (field_path examples: llm_routing.provider, llm_routing.temperature, llm_routing.model)

Respond with a JSON array of proposed edits (no markdown fences):
[
  {{
    "dimension": "prompt|tool|memory|control_flow|llm_routing",
    "field_path": "dotted.path.to.field",
    "operation": "replace|append|remove|set|adjust",
    "new_value": <the new value (string, number, boolean, or list)>,
    "rationale": "<why this edit addresses specific failures, citing cluster IDs>"
  }}
]

Rules:
- Propose 1-5 edits, ordered by expected impact
- Each edit must target a valid field path listed above
- Rationale must reference specific failure cluster examples
- For prompt edits, the new_value is the complete rewritten prompt template
- For ADJUST operations, new_value is a multiplier (e.g., 2.0 means double)
- Prefer small adjustments over complete rewrites
- If a cluster has count=1, only propose an edit if the pattern is clear"""


class Planner:
    """LLM-driven harness edit proposer.

    Reads failure clusters and the current harness config, then uses an LLM
    to propose concrete, typed HarnessEdits that address the failures.
    """

    def __init__(self, *, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider
        self.model = model

    async def propose(
        self,
        config: HarnessConfig,
        clusters: list,
        *,
        max_edits: int = 5,
    ) -> list[HarnessEdit]:
        """Propose harness edits to address the given failure clusters.

        Args:
            config: The current harness configuration.
            clusters: List of FailureCluster objects (or dicts with same shape).
            max_edits: Maximum number of edits to propose.

        Returns:
            List of typed HarnessEdit objects.
        """
        if not clusters:
            return []

        config_summary = self._summarise_config(config)
        clusters_text = self._format_clusters(clusters)

        prompt = _PLANNER_USER.format(
            config_summary=config_summary,
            clusters_text=clusters_text,
        )

        try:
            data, _resp = await _call_llm_json(
                [
                    ChatMessage(role=Role.SYSTEM, content=_PLANNER_SYSTEM),
                    ChatMessage(role=Role.USER, content=prompt),
                ],
                temperature=0.2,
                max_tokens=2000,
                retry_hint=(
                    "Respond with ONLY a JSON array of edits, no prose. Each edit: "
                    '{"dimension": "prompt|memory|control_flow|llm_routing|tool", '
                    '"field_path": "...", "operation": "replace|set|adjust|append|remove|merge", '
                    '"new_value": ..., "rationale": "..."}'
                ),
            )
            edits = self._parse_edits_from_json(data, config.config_id)
            return edits[:max_edits]
        except Exception as e:
            log.warning("Planner LLM call failed (using heuristic fallback): %s", e)
            return self._heuristic_edits(config, clusters, max_edits)

    def _summarise_config(self, config: HarnessConfig) -> str:
        """Produce a concise summary of the harness config for the LLM."""
        return json.dumps({
            "config_id": config.config_id,
            "version": config.version,
            "description": config.description,
            "tags": config.tags,
            "tools": config.tool_registry_names,
            "control_flow": {
                "max_steps": config.control_flow.max_steps,
                "max_tokens": config.control_flow.max_tokens,
                "max_seconds": config.control_flow.max_seconds,
                "replan_on_failure": config.control_flow.replan_on_failure,
                "replan_confidence_threshold": config.control_flow.replan_confidence_threshold,
            },
            "memory_policy": {
                "retrieval_k": config.memory_policy.retrieval_k,
                "vector_weight": config.memory_policy.vector_weight,
                "recency_weight": config.memory_policy.recency_weight,
                "importance_weight": config.memory_policy.importance_weight,
            },
            "llm_routing": {
                "provider": config.llm_routing.provider,
                "temperature": config.llm_routing.temperature,
            },
        }, indent=2)

    def _format_clusters(self, clusters: list) -> str:
        """Format failure clusters for the LLM prompt."""
        parts: list[str] = []
        for i, c in enumerate(clusters):
            if hasattr(c, "to_dict"):
                d = c.to_dict()
            elif isinstance(c, dict):
                d = c
            else:
                continue
            parts.append(
                f"Cluster {i + 1} [{d.get('severity', 'medium').upper()}]: "
                f"{d.get('failure_pattern', 'unknown')}\n"
                f"  Tool: {d.get('common_tool', 'unknown')}\n"
                f"  Category: {d.get('common_error_category', 'unknown')}\n"
                f"  Count: {d.get('count', 0)}\n"
                f"  Suggested dimension: {d.get('suggested_dimension', 'prompt')}\n"
                f"  Suggested fix: {d.get('suggested_fix', '')}\n"
                f"  Example: {d.get('examples', [{}])[0].get('error', '') if d.get('examples') else 'none'}"
            )
        return "\n\n".join(parts)

    def _parse_edits(self, content: str, config_id: str) -> list[HarnessEdit]:
        """Parse the LLM's JSON response into HarnessEdit objects."""
        content = normalize_llm_response(content)

        try:
            raw_edits = json.loads(content)
            if not isinstance(raw_edits, list):
                raw_edits = [raw_edits]
        except json.JSONDecodeError:
            start = content.find("[")
            end = content.rfind("]")
            if start != -1 and end != -1:
                try:
                    raw_edits = json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    return []
            else:
                return []
        return self._parse_edits_from_json(raw_edits, config_id)

    def _parse_edits_from_json(self, raw_edits, config_id: str) -> list[HarnessEdit]:
        """Convert already-parsed JSON (list or single dict) into HarnessEdit objects."""
        if isinstance(raw_edits, dict):
            raw_edits = [raw_edits]
        if not isinstance(raw_edits, list):
            return []

        edits: list[HarnessEdit] = []
        for re in raw_edits:
            if not isinstance(re, dict):
                continue
            try:
                dim_str = re.get("dimension", "prompt")
                op_str = re.get("operation", "replace")
                edit = HarnessEdit(
                    dimension=EditDimension(dim_str),
                    field_path=re.get("field_path", ""),
                    operation=EditOperation(op_str),
                    new_value=re.get("new_value"),
                    rationale=re.get("rationale", ""),
                    proposed_by="planner",
                    parent_config_id=config_id,
                )
                edits.append(edit)
            except (ValueError, TypeError) as e:
                log.warning("Skipping invalid edit proposal: %s", e)
                continue

        return edits

    def _heuristic_edits(
        self,
        config: HarnessConfig,
        clusters: list,
        max_edits: int,
    ) -> list[HarnessEdit]:
        """Fallback: generate simple heuristic edits without LLM."""
        edits: list[HarnessEdit] = []
        for c in clusters[:max_edits]:
            if hasattr(c, "to_dict"):
                d = c.to_dict()
            elif isinstance(c, dict):
                d = c
            else:
                continue
            dim = d.get("suggested_dimension", "prompt")
            cat = d.get("common_error_category", "unknown")
            tool = d.get("common_tool", "unknown")
            count = d.get("count", 0)

            if cat == "parse_error":
                edits.append(HarnessEdit(
                    dimension=EditDimension.PROMPT,
                    field_path="prompts.planner_user_template",
                    operation=EditOperation.REPLACE,
                    new_value="(improved planner prompt with clearer output format instructions)",
                    rationale=f"Heuristic: {count} parse errors on {tool} — planner prompt needs clearer JSON output spec",
                    proposed_by="planner_heuristic",
                    parent_config_id=config.config_id,
                ))
            elif cat == "timeout" or cat == "rate_limit":
                edits.append(HarnessEdit(
                    dimension=EditDimension.CONTROL_FLOW,
                    field_path="control_flow.max_seconds",
                    operation=EditOperation.ADJUST,
                    new_value=1.5,
                    rationale=f"Heuristic: {count} timeout/rate-limit errors — increase max_seconds by 50%",
                    proposed_by="planner_heuristic",
                    parent_config_id=config.config_id,
                ))
            elif cat == "empty_result":
                edits.append(HarnessEdit(
                    dimension=EditDimension.MEMORY,
                    field_path="memory_policy.retrieval_k",
                    operation=EditOperation.ADJUST,
                    new_value=1.5,
                    rationale=f"Heuristic: {count} empty result errors — increase retrieval_k for more context",
                    proposed_by="planner_heuristic",
                    parent_config_id=config.config_id,
                ))

        return edits


# ---------------------------------------------------------------------------
# Critic — counterfactual evaluation gates
# ---------------------------------------------------------------------------

_CRITIC_SYSTEM = """You are a harness evolution critic (AEGIS Critic). Your job is to evaluate whether a proposed harness edit would genuinely fix a given failure pattern. Think counterfactually: "If this edit were applied, would the failure still occur?"

Respond with JSON: {"verdict": "accepted"|"rejected", "confidence": 0.0-1.0, "feedback": "<reasoning>"}"""

_CRITIC_USER = """FAILURE PATTERN:
{cluster_summary}

PROPOSED EDIT:
- Dimension: {dimension}
- Field: {field_path}
- Operation: {operation}
- New value: {new_value}
- Rationale: {rationale}

Would applying this edit prevent the failure from recurring? Consider:
1. Does the edit actually address the root cause?
2. Could it introduce new failure modes?
3. Is the change proportional to the problem?"""


class Critic:
    """Counterfactual evaluation gates for proposed harness edits.

    The Critic filters edits proposed by the Planner, accepting only those
    that pass counterfactual reasoning. This prevents the evolution loop
    from applying harmful or irrelevant edits.

    See HarnessX paper §4.3 for the Critic role in AEGIS.
    """

    def __init__(self, acceptance_threshold: float = 0.6):
        self.acceptance_threshold = acceptance_threshold

    async def evaluate(
        self,
        edit: HarnessEdit,
        cluster: dict,
        config: HarnessConfig,
    ) -> dict:
        """Evaluate a single edit against a failure cluster.

        Returns: {"verdict": "accepted"|"rejected", "confidence": float, "feedback": str}
        """
        prompt = _CRITIC_USER.format(
            cluster_summary=json.dumps({
                "pattern": cluster.get("failure_pattern", ""),
                "tool": cluster.get("common_tool", ""),
                "category": cluster.get("common_error_category", ""),
                "count": cluster.get("count", 0),
                "severity": cluster.get("severity", "medium"),
            }, indent=2),
            dimension=edit.dimension.value,
            field_path=edit.field_path,
            operation=edit.operation.value,
            new_value=str(edit.new_value)[:500],
            rationale=edit.rationale,
        )
        try:
            result, _resp = await _call_llm_json(
                [
                    ChatMessage(role=Role.SYSTEM, content=_CRITIC_SYSTEM),
                    ChatMessage(role=Role.USER, content=prompt),
                ],
                temperature=0.1,
                # Generous budget so reasoning models (M3, R1, Qwen-thinking)
                # can fit both their internal think block AND the JSON
                # verdict. 300 was too tight — the model burned all tokens
                # on a think block and the JSON was truncated.
                max_tokens=800,
                retry_hint=(
                    "Respond with ONLY valid JSON: "
                    '{"verdict": "accepted"|"rejected", "confidence": 0.0-1.0, "feedback": "..."}'
                ),
            )
            if not isinstance(result, dict):
                raise ValueError(f"Critic returned non-dict JSON: {type(result).__name__}")
            return {
                "verdict": result.get("verdict", "rejected"),
                "confidence": float(result.get("confidence", 0.5)),
                "feedback": result.get("feedback", ""),
            }
        except Exception as e:
            log.warning("Critic LLM call failed (defaulting to accept): %s", e)
            return {
                "verdict": "accepted",
                "confidence": 0.5,
                "feedback": f"Critic unavailable: {e}. Defaulting to accept.",
            }

    async def filter_edits(
        self,
        edits: list[HarnessEdit],
        clusters: list,
        config: HarnessConfig,
    ) -> list[HarnessEdit]:
        """Filter edits through the critic, returning only accepted ones.

        Each edit is evaluated against its most relevant cluster.
        """
        if not edits:
            return []

        accepted: list[HarnessEdit] = []
        for edit in edits:
            best_cluster = self._match_cluster(edit, clusters)
            if best_cluster is None:
                accepted.append(edit)
                continue

            result = await self.evaluate(edit, best_cluster, config)
            edit.critic_verdict = result["verdict"]
            edit.critic_confidence = result["confidence"]
            edit.critic_feedback = result["feedback"]

            if result["verdict"] == "accepted" and result["confidence"] >= self.acceptance_threshold:
                accepted.append(edit)
                log.info("Critic ACCEPTED edit %s (confidence=%.2f)", edit.edit_id, result["confidence"])
            else:
                log.info("Critic REJECTED edit %s (confidence=%.2f): %s",
                         edit.edit_id, result["confidence"], result["feedback"])

        return accepted

    def _match_cluster(self, edit: HarnessEdit, clusters: list) -> Optional[dict]:
        """Match an edit to the most relevant failure cluster."""
        best: Optional[dict] = None
        best_score = -1.0
        for c in clusters:
            d = c.to_dict() if hasattr(c, "to_dict") else c
            score = 0.0
            if d.get("suggested_dimension") == edit.dimension.value:
                score += 0.5
            if d.get("common_tool", "") in edit.rationale:
                score += 0.3
            if d.get("severity") == "critical":
                score += 0.2
            if score > best_score:
                best_score = score
                best = d
        return best

    def counterfactual_score(
        self,
        edit: HarnessEdit,
        cluster: dict,
        config: HarnessConfig,
    ) -> float:
        """Synchronous scoring — returns 0.0-1.0 (intended for non-async contexts)."""
        if edit.critic_confidence is not None:
            return edit.critic_confidence if edit.critic_verdict == "accepted" else 0.0
        return 0.5


# ---------------------------------------------------------------------------
# Evolver — applies edits via substitution algebra
# ---------------------------------------------------------------------------

class Evolver:
    """Applies HarnessEdits to produce new HarnessConfig variants.

    Delegates to the substitution algebra (harness.apply_edits) and persists
    the resulting configs to the HarnessConfigStore.
    """

    def __init__(self, *, store_results: bool = True):
        self.store_results = store_results
        self._store = get_config_store()

    def evolve(
        self,
        config: HarnessConfig,
        edits: list[HarnessEdit],
        *,
        tag: Optional[str] = None,
    ) -> HarnessConfig:
        """Apply edits to a config and return the evolved config.

        Args:
            config: The source config (not mutated).
            edits: List of edits to apply.
            tag: Optional tag to add to the evolved config.

        Returns:
            A new HarnessConfig with edits applied.
        """
        if not edits:
            return config.clone()

        new_config = apply_edits(config, edits)
        new_config.evolution_round = config.evolution_round + 1
        new_config.description = (
            f"Evolved from {config.config_id} — {len(edits)} edits applied"
        )
        if tag:
            new_config.tags = list(set(config.tags) | {tag})

        # Recompute config_id so it reflects the final content (the field edits from
        # apply_edits plus the description/tag mutations above). apply_edit hashes
        # pre-mutation state, so without this recompute, distinct variants from the
        # same base share one config_id and collapse in the buffer and config store.
        new_config.config_id = new_config._compute_hash()

        if self.store_results:
            self._store.save(new_config)

        log.info(
            "Evolved config %s (%d edits, round %d, tags=%s)",
            new_config.config_id, len(edits), new_config.evolution_round, new_config.tags,
        )
        return new_config

    async def evolve_from_clusters(
        self,
        config: HarnessConfig,
        clusters: list,
        planner: Planner,
        *,
        critic: Optional[Critic] = None,
    ) -> HarnessConfig:
        """Full evolve-from-clusters flow: clusters → planner → critic → apply.

        Args:
            config: Source harness config.
            clusters: Failure clusters from Digester.
            planner: Planner instance for proposing edits.
            critic: Optional Critic for gating edits.

        Returns:
            Evolved HarnessConfig (or clone of source if no edits accepted).
        """
        edits = await planner.propose(config, clusters)
        if not edits:
            return config.clone()

        if critic:
            edits = await critic.filter_edits(edits, clusters, config)

        if not edits:
            log.info("No edits survived critic — returning clone of %s", config.config_id)
            return config.clone()

        return self.evolve(config, edits)

    def produce_variants(
        self,
        base_config: HarnessConfig,
        edit_sets: list[list[HarnessEdit]],
    ) -> list[HarnessConfig]:
        """Produce N config variants from N sets of edits (for ensemble routing)."""
        variants: list[HarnessConfig] = []
        for i, edits in enumerate(edit_sets):
            if not edits:
                variants.append(base_config.clone())
                continue
            variant = self.evolve(base_config, edits, tag=f"variant_{i}")
            variants.append(variant)
        return variants


# ---------------------------------------------------------------------------
# EnsembleRunner — runs N config variants in parallel
# ---------------------------------------------------------------------------

@dataclass
class VariantResult:
    """Result of running a single config variant against the eval suite."""
    config_id: str
    config: HarnessConfig
    success_rate: float = 0.0
    avg_duration_ms: float = 0.0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    passed: int = 0
    total: int = 0
    error: Optional[str] = None


class EnsembleRunner:
    """Runs N harness config variants against an eval suite in parallel.

    This implements the variant isolation strategy from HarnessX §4.5.
    Configs compete on the same eval suite; the best performer is selected
    for the next evolution round.
    """

    def __init__(
        self,
        agent_factory: Callable[[HarnessConfig], Any],
        tasks: list,
        *,
        max_concurrency: int = 4,
    ):
        self.agent_factory = agent_factory
        self.tasks = tasks
        self.max_concurrency = max_concurrency

    async def run_round(
        self,
        configs: list[HarnessConfig],
    ) -> list[VariantResult]:
        """Run all config variants against the eval suite.

        Args:
            configs: List of config variants to evaluate.

        Returns:
            List of VariantResult, sorted by success_rate descending.
        """
        if not configs:
            return []

        sem = asyncio.Semaphore(self.max_concurrency)

        async def _run_one(cfg: HarnessConfig) -> VariantResult:
            async with sem:
                return await self._run_config(cfg)

        results = await asyncio.gather(*[_run_one(c) for c in configs])
        results.sort(key=lambda r: r.success_rate, reverse=True)
        return results

    async def _run_config(self, config: HarnessConfig) -> VariantResult:
        """Run a single config against all tasks."""
        agent = self.agent_factory(config)
        passed = 0
        total = len(self.tasks)
        total_duration = 0.0

        for task in self.tasks:
            try:
                query = task.prompt if hasattr(task, "prompt") else str(task)
                result = await agent.run_to_completion(query)
                if result.success:
                    passed += 1
                total_duration += result.duration_ms
            except Exception as e:
                log.warning("Task failed for config %s: %s", config.config_id, e)

        success_rate = passed / max(1, total)
        return VariantResult(
            config_id=config.config_id,
            config=config,
            success_rate=success_rate,
            avg_duration_ms=total_duration / max(1, total),
            passed=passed,
            total=total,
        )

    def compare_variants(
        self,
        results: list[VariantResult],
    ) -> list[tuple[HarnessConfig, float]]:
        """Return configs sorted by success_rate (best first)."""
        return [(r.config, r.success_rate) for r in sorted(results, key=lambda r: r.success_rate, reverse=True)]

    def select_best(self, results: list[VariantResult]) -> Optional[HarnessConfig]:
        """Return the config with the highest success_rate."""
        if not results:
            return None
        best = max(results, key=lambda r: r.success_rate)
        log.info("Best variant: %s (success_rate=%.2f, %d/%d)", best.config_id, best.success_rate, best.passed, best.total)
        return best.config


# ---------------------------------------------------------------------------
# Evolution Result Types
# ---------------------------------------------------------------------------

@dataclass
class RoundResult:
    """Outcome of a single evolution round."""
    round_number: int
    config: HarnessConfig
    success_rate: float
    edits_applied: int = 0
    duration_seconds: float = 0.0
    variants_tested: int = 1
    clusters_found: int = 0
    notes: str = ""


@dataclass
class EvolutionResult:
    """Complete result of an AEGIS evolution run."""
    run_id: str
    base_config: HarnessConfig
    rounds: list[RoundResult] = field(default_factory=list)
    best_config: Optional[HarnessConfig] = None
    best_score: float = 0.0
    baseline_score: float = 0.0
    improvement: float = 0.0
    total_edits: int = 0
    total_duration_seconds: float = 0.0
    converged: bool = False
    converged_at_round: int = -1

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "base_config_id": self.base_config.config_id,
            "baseline_score": self.baseline_score,
            "best_score": self.best_score,
            "improvement": self.improvement,
            "improvement_pct": f"{self.improvement * 100:.1f}%",
            "total_rounds": len(self.rounds),
            "total_edits": self.total_edits,
            "total_duration_seconds": self.total_duration_seconds,
            "converged": self.converged,
            "converged_at_round": self.converged_at_round,
            "rounds": [{
                "round": r.round_number,
                "config_id": r.config.config_id,
                "success_rate": r.success_rate,
                "edits_applied": r.edits_applied,
                "variants_tested": r.variants_tested,
                "notes": r.notes,
            } for r in self.rounds],
        }

    @property
    def summary(self) -> str:
        return (
            f"Evolution run {self.run_id}: "
            f"{self.baseline_score:.2f} → {self.best_score:.2f} "
            f"(+{self.improvement * 100:.1f}%) over {len(self.rounds)} rounds"
        )


# ---------------------------------------------------------------------------
# EvolutionLoop — orchestrates the full AEGIS cycle
# ---------------------------------------------------------------------------

_CONVERGENCE_WINDOW = 3  # rounds without improvement to declare convergence


class EvolutionLoop:
    """Orchestrates the full AEGIS trace-driven evolution cycle.

    The loop:

    1. **Round 0 (Baseline)**: Run base_config against eval suite
    2. **For each round**:
       a. Digester: cluster failures from previous round
       b. Planner: propose harness edits from clusters
       c. Critic: filter edits via counterfactual evaluation
       d. Evolver: apply accepted edits → new config variant(s)
       e. EnsembleRunner: run variant(s) against eval suite
       f. Select best → becomes base_config for next round
    3. Stop when: max_rounds reached OR convergence detected

    Usage::

        from backend.agent.evolution import EvolutionLoop
        from backend.agent.harness_defaults import get_preset
        from backend.agent import Agent

        base = get_preset("research")
        tasks = [Task(id="t1", suite="my_suite", prompt="...", expected="...", use_agent=True)]

        def factory(cfg):
            return Agent(harness_config=cfg)

        loop = EvolutionLoop(base, tasks, factory, max_rounds=5)
        result = await loop.run()
        print(result.summary)
    """

    def __init__(
        self,
        base_config: HarnessConfig,
        tasks: list,
        agent_factory: Callable[[HarnessConfig], Any],
        *,
        max_rounds: int = 10,
        variants_per_round: int = 1,
        convergence_window: int = _CONVERGENCE_WINDOW,
        planner: Optional[Planner] = None,
        critic: Optional[Critic] = None,
        digester: Optional[Any] = None,
    ):
        self.base_config = base_config
        self.tasks = tasks
        self.agent_factory = agent_factory
        self.max_rounds = max_rounds
        self.variants_per_round = variants_per_round
        self.convergence_window = convergence_window

        self.planner = planner or Planner()
        self.critic = critic or Critic()
        self.evolver = Evolver()

        # Lazy import digester to avoid circular imports
        if digester is None:
            from backend.agent.digester import Digester
            self.digester = Digester()
        else:
            self.digester = digester

        self.runner = EnsembleRunner(
            agent_factory=agent_factory,
            tasks=tasks,
            max_concurrency=min(4, variants_per_round),
        )

        self._run_id: str = ""
        self._history: list[Any] = []  # AgentRunResult history

    async def run(self) -> EvolutionResult:
        """Execute the full AEGIS evolution cycle."""
        self._run_id = uuid.uuid4().hex[:12]
        t0 = time.time()
        result = EvolutionResult(
            run_id=self._run_id,
            base_config=self.base_config,
        )

        log.info("=== AEGIS Evolution Loop %s === Round 0 (baseline)", self._run_id)

        # Round 0: Baseline
        baseline_results = await self.runner.run_round([self.base_config])
        baseline_vr = baseline_results[0] if baseline_results else None
        baseline_score = baseline_vr.success_rate if baseline_vr else 0.0

        result.baseline_score = baseline_score
        result.rounds.append(RoundResult(
            round_number=0,
            config=self.base_config,
            success_rate=baseline_score,
            notes="baseline",
        ))

        current_config = self.base_config
        current_score = baseline_score
        best_config = self.base_config
        best_score = baseline_score
        rounds_without_improvement = 0

        # Evolution rounds
        for rnd in range(1, self.max_rounds + 1):
            rnd_t0 = time.time()
            log.info("--- Round %d/%d (score=%.3f) ---", rnd, self.max_rounds, current_score)

            # Step A: Digest failures
            clusters = await self.digester.digest(self._history)
            if not clusters:
                log.info("No failures to digest — skipping round %d", rnd)
                rounds_without_improvement += 1
                if rounds_without_improvement >= self.convergence_window:
                    result.converged = True
                    result.converged_at_round = rnd
                    break
                continue

            log.info("Digested %d failure clusters", len(clusters))

            # Step B: Planner → propose edits
            edits = await self.planner.propose(current_config, clusters)
            if not edits:
                log.info("Planner proposed no edits — skipping round %d", rnd)
                rounds_without_improvement += 1
                if rounds_without_improvement >= self.convergence_window:
                    break
                continue

            log.info("Planner proposed %d edits", len(edits))

            # Step C: Critic → filter edits
            accepted = await self.critic.filter_edits(edits, clusters, current_config)
            if not accepted:
                log.info("Critic rejected all edits — round %d", rnd)
                rounds_without_improvement += 1
                if rounds_without_improvement >= self.convergence_window:
                    break
                continue

            log.info("Critic accepted %d/%d edits", len(accepted), len(edits))

            # Step D: Evolver → produce variants
            variants: list[HarnessConfig] = []
            if self.variants_per_round <= 1:
                new_config = self.evolver.evolve(current_config, accepted)
                variants = [new_config]
            else:
                for v in range(self.variants_per_round):
                    variant_edits = accepted[:max(1, len(accepted) - v)] if v > 0 else accepted
                    variant = self.evolver.evolve(current_config, variant_edits, tag=f"variant_{rnd}_{v}")
                    variants.append(variant)

            # Step E: EnsembleRunner → evaluate variants
            variant_results = await self.runner.run_round(variants)
            best_vr = variant_results[0] if variant_results else None

            if best_vr is None:
                log.warning("No variant results for round %d", rnd)
                rounds_without_improvement += 1
                continue

            round_score = best_vr.success_rate
            round_config = best_vr.config
            rnd_duration = time.time() - rnd_t0

            result.rounds.append(RoundResult(
                round_number=rnd,
                config=round_config,
                success_rate=round_score,
                edits_applied=len(accepted),
                duration_seconds=rnd_duration,
                variants_tested=len(variants),
                clusters_found=len(clusters),
                notes=f"edits: {', '.join(e.edit_id for e in accepted[:3])}",
            ))

            # Step F: Select best → update state
            if round_score > best_score:
                best_config = round_config
                best_score = round_score
                rounds_without_improvement = 0
                log.info("NEW BEST: %.3f (was %.3f)", round_score, best_score)
            else:
                rounds_without_improvement += 1
                log.info("No improvement: %.3f (best: %.3f, no-improve: %d)",
                         round_score, best_score, rounds_without_improvement)

            current_config = round_config
            current_score = round_score

            if rounds_without_improvement >= self.convergence_window:
                result.converged = True
                result.converged_at_round = rnd
                log.info("Converged after %d rounds without improvement", rounds_without_improvement)
                break

        # Finalise result
        result.best_config = best_config
        result.best_score = best_score
        result.improvement = best_score - baseline_score
        result.total_edits = sum(r.edits_applied for r in result.rounds)
        result.total_duration_seconds = time.time() - t0

        log.info(
            "=== Evolution Complete: %.3f → %.3f (+%.1f%%) in %d rounds (%.0fs) ===",
            baseline_score, best_score, result.improvement * 100,
            len(result.rounds), result.total_duration_seconds,
        )

        return result

    def append_history(self, results: list) -> None:
        """Append agent run results to the evolution history."""
        self._history.extend(results)


# ---------------------------------------------------------------------------
# Singleton shortcuts
# ---------------------------------------------------------------------------

def quick_planner() -> Planner:
    return Planner()


def quick_critic(threshold: float = 0.6) -> Critic:
    return Critic(acceptance_threshold=threshold)


def quick_evolver(store: bool = True) -> Evolver:
    return Evolver(store_results=store)
