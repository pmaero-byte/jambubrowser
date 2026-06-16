"""
Harness Configuration — Typed, Composable, and Validatable Harness Primitives
=============================================================================

Implements the HarnessX-style composition algebra where the agent harness
(prompts, tools, memory, control flow, LLM routing) is a first-class typed
object. Supports:

- **HarnessConfig** — serialisable snapshot of every harness dimension
- **MemoryPolicy** — which memory stores to use and retrieval weights
- **ControlFlowSpec** — budget, replanning, verification parameters
- **HarnessEdit** — typed edits with a substitution algebra (apply/edit/revert)
- **Config validation** — ensures all tool refs, model refs, and weights are valid

This is the foundation for AEGIS (trace-driven harness evolution). The evolution
pipeline reads HarnessConfig from agent runs, produces HarnessEdits, and applies
them to produce new config variants.

Phase 1 of the HarnessX integration roadmap.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Memory Policy
# ---------------------------------------------------------------------------

@dataclass
class MemoryPolicy:
    """Which memory stores to query and how to weight retrieval results.

    Maps to Jambubrowser's 4-store memory system (backend/memory/store.py).
    """
    use_user_profile: bool = True           # interests, expertise, preferences
    use_session_memory: bool = True         # active session context
    use_semantic_memory: bool = True        # vector-embedded facts/learnings
    use_procedural_memory: bool = True      # task_pattern → approach success rates

    retrieval_k: int = 10                   # top-k memories to retrieve
    vector_weight: float = 0.6              # 0-1, semantic similarity weight
    recency_weight: float = 0.2             # 0-1, temporal decay weight
    importance_weight: float = 0.1          # 0-1, explicit importance weight
    fts_weight: float = 0.1                 # 0-1, keyword / profile match weight
    recency_tau_days: float = 14.0          # recency half-life in days

    def validate(self) -> list[str]:
        errors: list[str] = []
        total = self.vector_weight + self.recency_weight + self.importance_weight + self.fts_weight
        if abs(total - 1.0) > 0.01:
            errors.append(f"Retrieval weights must sum to 1.0, got {total}")
        if self.retrieval_k < 1 or self.retrieval_k > 100:
            errors.append(f"retrieval_k must be 1-100, got {self.retrieval_k}")
        if self.recency_tau_days <= 0:
            errors.append(f"recency_tau_days must be positive")
        return errors


# ---------------------------------------------------------------------------
# Control Flow Specification
# ---------------------------------------------------------------------------

@dataclass
class ControlFlowSpec:
    """Budget, replanning, and verification parameters for the agent loop."""
    max_steps: int = 10                     # maximum tool calls per run
    max_tokens: int = 30000                 # token budget per run
    max_seconds: float = 120.0              # wall-clock budget per run

    plan_strategy: str = "decompose"        # "decompose" | "single_step" | "tree_search"
    replan_on_failure: bool = True          # auto-replan after failed/blocked step
    replan_on_weak_progress: bool = True    # replan when verifier confidence < threshold
    replan_confidence_threshold: float = 0.7  # below this, trigger replan

    verify_after_each_step: bool = True     # LLM-as-judge verification after every tool call
    verifier_temperature: float = 0.1       # low = deterministic verdicts
    verifier_model: Optional[str] = None    # None = use default provider model

    synthesize_final_answer: bool = True    # auto-synthesize if no final_answer step
    synthesis_temperature: float = 0.3
    synthesis_max_tokens: int = 800

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.max_steps < 1 or self.max_steps > 50:
            errors.append(f"max_steps must be 1-50, got {self.max_steps}")
        if self.max_tokens < 100 or self.max_tokens > 200000:
            errors.append(f"max_tokens must be 100-200000, got {self.max_tokens}")
        if self.max_seconds < 1.0 or self.max_seconds > 600.0:
            errors.append(f"max_seconds must be 1-600, got {self.max_seconds}")
        if self.plan_strategy not in ("decompose", "single_step", "tree_search"):
            errors.append(f"Unknown plan_strategy: {self.plan_strategy}")
        if not 0.0 <= self.replan_confidence_threshold <= 1.0:
            errors.append(f"replan_confidence_threshold must be 0-1")
        return errors


# ---------------------------------------------------------------------------
# LLM Routing Specification
# ---------------------------------------------------------------------------

@dataclass
class LLMRoutingSpec:
    """Which provider/model to use for which agent sub-task."""
    provider: str = "auto"                  # "auto" | "anthropic" | "openai" | "ollama" | "mlx" | "minimax"
    model: Optional[str] = None             # None = use provider default
    routing_strategy: str = "fallback"      # "fallback" | "cheapest" | "fastest" | "quality" | "local_only"

    planner_provider: Optional[str] = None  # override for plan decomposition
    planner_model: Optional[str] = None
    verifier_provider: Optional[str] = None # override for step verification
    verifier_model: Optional[str] = None
    synthesizer_provider: Optional[str] = None  # override for final synthesis
    synthesizer_model: Optional[str] = None

    temperature: float = 0.3                # default temperature for most calls
    tool_use_temperature: float = 0.2       # lower = more deterministic tool selection

    def validate(self) -> list[str]:
        errors: list[str] = []
        valid_providers = {"auto", "anthropic", "openai", "ollama", "mlx", "minimax", "mock"}
        if self.provider not in valid_providers:
            errors.append(f"Unknown provider: {self.provider}")
        valid_strategies = {"fallback", "cheapest", "fastest", "quality", "local_only"}
        if self.routing_strategy not in valid_strategies:
            errors.append(f"Unknown routing_strategy: {self.routing_strategy}")
        if not 0.0 <= self.temperature <= 1.0:
            errors.append(f"temperature must be 0-1")
        return errors


# ---------------------------------------------------------------------------
# Prompt Templates
# ---------------------------------------------------------------------------

@dataclass
class PromptConfig:
    """Configurable prompt templates for each agent sub-task.

    These are the textual "soul" of the harness — what the LLM sees at each
    stage. The AEGIS Planner can rewrite these based on failure traces.
    """
    planner_system: str = ""
    planner_user_template: str = ""          # {tool_descriptions}, {query}, {user_context}, {max_steps}

    verifier_system: str = ""
    verifier_user_template: str = ""         # {goal}, {step}, {result}, {remaining}

    replanner_system: str = ""
    replanner_user_template: str = ""        # {query}, {failed_step}, {verdict}, {tool_descriptions}

    synthesis_system: str = ""
    synthesis_user_template: str = ""        # {query}, {observations}

    task_agent_system: str = ""              # system prompt for the task-performing agent itself

    @classmethod
    def defaults(cls) -> "PromptConfig":
        """Return the default prompt templates matching the current loop.py / plan.py / verifier.py."""
        return cls(
            planner_user_template=(
                "You are a planning module for an AI research agent. Given a user's goal "
                "and a list of available tools, produce a step-by-step plan to achieve the goal.\n\n"
                "Available tools:\n{tool_descriptions}\n\n"
                "User goal: {query}\n\n{user_context}\n\n"
                'Respond with a JSON object (no markdown fencing) of the form:\n'
                '{{"steps": [{{"description": "<plain English>", "tool": "<tool_name or null>", "args": {{...}} }}, ...]}}\n\n'
                "Rules:\n"
                "- 1 to {max_steps} steps, no more\n"
                "- Each step must either call a tool OR be a final synthesis step (tool=null, args={{\"text\": \"<final answer>\"}})\n"
                "- The last step's tool should typically be \"final_answer\" with text=\"<your final answer to the user>\"\n"
                "- Be concrete. If the goal requires research, include a \"web_search\" step first.\n"
                "- If the goal is just a question, you can have a single \"final_answer\" step."
            ),
            verifier_user_template=(
                "You are a verification module. Given:\n"
                "- The user's goal\n"
                "- A step that was just executed (description + tool + result)\n"
                "- The remaining plan\n\n"
                "Decide whether the step meaningfully advanced the goal, OR was a dead end.\n\n"
                'Respond with JSON (no markdown):\n'
                '{{"advanced": true | false, "confidence": 0.0-1.0, "feedback": "<why>", "suggested_next_action": "<what to do next>"}}\n\n'
                "Goal: {goal}\nStep: {step}\nResult: {result}\nRemaining: {remaining}"
            ),
            replanner_user_template=(
                "You are replanning after a failed step. Given:\n"
                "- The original goal\n"
                "- The plan so far with one step that failed\n"
                "- A verdict explaining why it failed\n"
                "Produce a revised plan (JSON, same format) that avoids the failure and still achieves the goal.\n\n"
                "Goal: {query}\nFailed step: {failed_step}\nFailure: {verdict}\n\n"
                "Available tools:\n{tool_descriptions}\n\n"
                'Respond with: {{"steps": [...]}}'
            ),
            synthesis_user_template=(
                "User asked: {query}\n\n"
                "Tool observations:\n{observations}\n\n"
                "Based on the observations, write a clear final answer to the user. "
                "Cite specific sources if any URLs were collected. Be concise."
            ),
        )


# ---------------------------------------------------------------------------
# Harness Configuration (the first-class object)
# ---------------------------------------------------------------------------

@dataclass
class HarnessConfig:
    """Complete typed harness configuration — the 'genome' of an agent run.

    Every field controls one dimension of the runtime harness. Serialisable,
    diffable, and validatable. This is what the AEGIS evolution engine reads
    from traces and edits to produce new variants.

    See HarnessX paper §3.1-3.3 for the theoretical foundation.
    """
    # Identity
    config_id: str = ""                     # unique hash of config content
    version: int = 1                        # monotonically increasing per evolution round
    parent_id: Optional[str] = None         # config_id of the config this was derived from
    created_at: float = field(default_factory=time.time)
    description: str = ""

    # Prompts — the textual interface
    prompts: PromptConfig = field(default_factory=PromptConfig.defaults)

    # Tools — which tools are available
    tool_registry_names: list[str] = field(default_factory=list)
    # populated from ToolRegistry.list_names() at config-creation time

    # Memory — how the agent remembers
    memory_policy: MemoryPolicy = field(default_factory=MemoryPolicy)

    # Control flow — the agent loop algorithm
    control_flow: ControlFlowSpec = field(default_factory=ControlFlowSpec)

    # LLM — which models power the agent
    llm_routing: LLMRoutingSpec = field(default_factory=LLMRoutingSpec)

    # Evolution metadata
    evolution_round: int = 0                # which AEGIS round produced this
    success_rate: Optional[float] = None    # measured on eval suite after this config
    tags: list[str] = field(default_factory=list)  # "research", "browser", "coding", "privacy-max"

    def __post_init__(self):
        if not self.config_id:
            self.config_id = self._compute_hash()

    def _compute_hash(self) -> str:
        """Deterministic hash over all config fields except identity."""
        d = self.to_dict()
        d.pop("config_id", None)
        d.pop("version", None)
        d.pop("parent_id", None)
        d.pop("created_at", None)
        d.pop("success_rate", None)
        d.pop("evolution_round", None)
        raw = json.dumps(d, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "config_id": self.config_id,
            "version": self.version,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "description": self.description,
            "prompts": asdict(self.prompts),
            "tool_registry_names": list(self.tool_registry_names),
            "memory_policy": asdict(self.memory_policy),
            "control_flow": asdict(self.control_flow),
            "llm_routing": asdict(self.llm_routing),
            "evolution_round": self.evolution_round,
            "success_rate": self.success_rate,
            "tags": list(self.tags),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def clone(self) -> "HarnessConfig":
        """Deep copy, generating a new config_id and incrementing version."""
        new = copy.deepcopy(self)
        new.config_id = ""
        new.version = self.version + 1
        new.parent_id = self.config_id
        new.created_at = time.time()
        new.success_rate = None
        new.__post_init__()
        return new

    def validate(self, available_tool_names: Optional[list[str]] = None) -> list[str]:
        """Validate all dimensions. Returns list of error strings (empty = valid).

        Args:
            available_tool_names: If provided, checks that all tool_registry_names
                                  reference actually-registered tools.
        """
        errors: list[str] = []
        errors.extend(self.memory_policy.validate())
        errors.extend(self.control_flow.validate())
        errors.extend(self.llm_routing.validate())
        if available_tool_names is not None:
            avail = set(available_tool_names)
            for tool_name in self.tool_registry_names:
                if tool_name not in avail:
                    errors.append(f"Tool {tool_name!r} in config not found in registry. Available: {sorted(avail)}")
        return errors


# ---------------------------------------------------------------------------
# Harness Edit — Substitution Algebra
# ---------------------------------------------------------------------------

class EditDimension(str, Enum):
    """Which harness dimension an edit targets."""
    PROMPT = "prompt"
    TOOL = "tool"
    MEMORY = "memory"
    CONTROL_FLOW = "control_flow"
    LLM_ROUTING = "llm_routing"

    @classmethod
    def detect(cls, edit_desc: str) -> "EditDimension":
        """Detect which dimension an edit description targets."""
        desc_lower = edit_desc.lower()
        if any(w in desc_lower for w in ("prompt", "system message", "instruction", "template")):
            return cls.PROMPT
        if any(w in desc_lower for w in ("tool", "register", "unregister", "swap")):
            return cls.TOOL
        if any(w in desc_lower for w in ("memory", "retrieval", "weight", "recall", "importance")):
            return cls.MEMORY
        if any(w in desc_lower for w in ("max_step", "max_token", "max_second", "replan", "verif", "control")):
            return cls.CONTROL_FLOW
        if any(w in desc_lower for w in ("model", "provider", "routing", "llm", "temperature")):
            return cls.LLM_ROUTING
        return cls.PROMPT  # default


class EditOperation(str, Enum):
    """The type of substitution to apply."""
    REPLACE = "replace"       # replace a field value entirely
    APPEND = "append"         # append to a list field
    REMOVE = "remove"         # remove from a list field
    SET = "set"               # set a scalar field
    MERGE = "merge"           # merge dict fields
    ADJUST = "adjust"         # numeric adjustment (multiply/add)


@dataclass
class HarnessEdit:
    """A single typed edit to a HarnessConfig. Supports the substitution algebra.

    Each edit targets one dimension, one field path within that dimension, and
    applies one operation. The Evolver produces these; the Critic gates them.

    See HarnessX paper §4.1 (Operational Mirror) and §4.3 (AEGIS Architecture).
    """
    # Identity
    edit_id: str = ""
    round_number: int = 0                   # which AEGIS round proposed this
    parent_config_id: str = ""              # config this edit was proposed for

    # Target
    dimension: EditDimension = EditDimension.PROMPT
    field_path: str = ""                    # dotted path, e.g. "prompts.verifier_user_template"
    operation: EditOperation = EditOperation.REPLACE

    # Payload
    old_value: Any = None                   # value before edit (for revert / diff)
    new_value: Any = None                   # value after edit

    # Provenance
    rationale: str = ""                     # why this edit was proposed
    proposed_by: str = ""                   # which AEGIS agent proposed it (digester/planner/evolver)
    failure_cluster_id: Optional[str] = None  # which failure cluster motivated this
    critic_verdict: Optional[str] = None    # "accepted" | "rejected" | "pending"
    critic_confidence: Optional[float] = None
    critic_feedback: Optional[str] = None

    def __post_init__(self):
        if not self.edit_id:
            raw = f"{self.parent_config_id}:{self.dimension.value}:{self.field_path}:{self.operation.value}:{hashlib.md5(str(self.new_value).encode()).hexdigest()[:8]}"
            self.edit_id = hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "edit_id": self.edit_id,
            "round_number": self.round_number,
            "parent_config_id": self.parent_config_id,
            "dimension": self.dimension.value,
            "field_path": self.field_path,
            "operation": self.operation.value,
            "rationale": self.rationale,
            "proposed_by": self.proposed_by,
            "failure_cluster_id": self.failure_cluster_id,
            "critic_verdict": self.critic_verdict,
            "critic_confidence": self.critic_confidence,
            "critic_feedback": self.critic_feedback,
        }

    def describe(self) -> str:
        """Human-readable description of this edit."""
        return (
            f"[{self.dimension.value}] {self.operation.value} "
            f"`{self.field_path}`: {self.rationale[:120]}"
        )


# ---------------------------------------------------------------------------
# Substitution Algebra — Pure Functions on HarnessConfig
# ---------------------------------------------------------------------------

def apply_edit(config: HarnessConfig, edit: HarnessEdit) -> HarnessConfig:
    """Apply a single HarnessEdit to a HarnessConfig, returning a new config.

    This is the core substitution algebra operation. Each edit targets a
    specific field_path within a dimension, applies an operation, and produces
    a clone of the config with the change applied.

    Args:
        config: The source config (not mutated)
        edit: The edit to apply

    Returns:
        A new HarnessConfig with the edit applied (clone + change).

    Raises:
        ValueError: If the field_path is invalid or operation unsupported.
    """
    new_config = config.clone()
    new_config.evolution_round = config.evolution_round + 1
    new_config.tags = list(config.tags)

    # Navigate to the target dimension object
    dim_map = {
        EditDimension.PROMPT: new_config.prompts,
        EditDimension.MEMORY: new_config.memory_policy,
        EditDimension.CONTROL_FLOW: new_config.control_flow,
        EditDimension.LLM_ROUTING: new_config.llm_routing,
    }

    # For TOOL dimension, we modify tool_registry_names list
    if edit.dimension == EditDimension.TOOL:
        _apply_tool_edit(new_config, edit)
        return new_config

    target = dim_map.get(edit.dimension)
    if target is None:
        raise ValueError(f"Unknown edit dimension: {edit.dimension}")

    # Resolve field_path (may be dotted for nested fields)
    obj, field = _resolve_field(target, edit.field_path)
    if not hasattr(obj, field):
        raise ValueError(f"Field {field!r} not found on {type(obj).__name__} (path: {edit.field_path})")

    _apply_field_edit(obj, field, edit)
    # Recompute config_id so it reflects the edited content. clone() above hashed
    # the *pre-edit* state; without this recompute, every edit derived from the
    # same base collapses to one config_id — breaking variant isolation, the
    # cross-harness replay buffer, and HarnessConfigStore (which keys by id).
    new_config.config_id = new_config._compute_hash()
    return new_config


def apply_edits(config: HarnessConfig, edits: list[HarnessEdit]) -> HarnessConfig:
    """Apply multiple edits sequentially, returning the final config."""
    current = config
    for edit in edits:
        current = apply_edit(current, edit)
    return current


def revert_edit(config: HarnessConfig, edit: HarnessEdit) -> HarnessConfig:
    """Revert an edit by applying its operation-appropriate inverse.

    The inverse depends on the operation:
      - ADJUST   (multiply by n)  -> multiply by 1/n
      - APPEND   (add item)       -> REMOVE that item
      - REMOVE   (drop item)      -> APPEND it back
      - REPLACE/SET/MERGE          -> swap old_value <-> new_value
    """
    if edit.operation == EditOperation.ADJUST:
        if not isinstance(edit.new_value, (int, float)) or edit.new_value == 0:
            raise ValueError(
                "Cannot revert ADJUST without a non-zero numeric new_value "
                f"(got {edit.new_value!r})"
            )
        inverse = HarnessEdit(
            dimension=edit.dimension,
            field_path=edit.field_path,
            operation=EditOperation.ADJUST,
            new_value=1.0 / edit.new_value,
            rationale=f"Revert (inverse ADJUST): {edit.edit_id}",
            proposed_by="revert",
        )
    elif edit.operation == EditOperation.APPEND:
        inverse = HarnessEdit(
            dimension=edit.dimension,
            field_path=edit.field_path,
            operation=EditOperation.REMOVE,
            old_value=edit.new_value,
            rationale=f"Revert (inverse APPEND): {edit.edit_id}",
            proposed_by="revert",
        )
    elif edit.operation == EditOperation.REMOVE:
        inverse = HarnessEdit(
            dimension=edit.dimension,
            field_path=edit.field_path,
            operation=EditOperation.APPEND,
            new_value=edit.old_value,
            rationale=f"Revert (inverse REMOVE): {edit.edit_id}",
            proposed_by="revert",
        )
    else:  # REPLACE / SET / MERGE — swap old_value <-> new_value
        inverse = HarnessEdit(
            dimension=edit.dimension,
            field_path=edit.field_path,
            operation=edit.operation,
            old_value=edit.new_value,
            new_value=edit.old_value,
            rationale=f"Revert: {edit.edit_id}",
            proposed_by="revert",
        )
    inverse.parent_config_id = config.config_id
    return apply_edit(config, inverse)


def diff_configs(a: HarnessConfig, b: HarnessConfig) -> list[HarnessEdit]:
    """Compute the minimal set of HarnessEdits to transform config A into config B.

    This enables the AEGIS Critic to reason about "what changed between rounds"
    and the Digester to attribute performance deltas to specific edits.
    """
    edits: list[HarnessEdit] = []
    a_dict = a.to_dict()
    b_dict = b.to_dict()
    for key in sorted(a_dict.keys()):
        if key in ("config_id", "version", "parent_id", "created_at", "success_rate"):
            continue
        if a_dict[key] != b_dict[key]:
            edits.append(HarnessEdit(
                dimension=EditDimension.detect(key),
                field_path=key,
                operation=EditOperation.REPLACE,
                old_value=a_dict[key],
                new_value=b_dict[key],
                rationale=f"Config evolved: {key} changed",
                proposed_by="diff",
            ))
    return edits


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_field(obj: Any, path: str) -> tuple[Any, str]:
    """Resolve a dotted path on an object, returning (parent_obj, field_name).

    The first part of the path may repeat the dimension name (e.g.,
    "control_flow.max_steps" when target is already the ControlFlowSpec).
    We skip the first segment if it matches the target's type name.
    """
    parts = path.split(".")

    # If the first segment is the dimension name of the target type, skip it
    type_name = type(obj).__name__.lower()
    dim_prefix = {
        "promptconfig": "prompts",
        "memorypolicy": "memory_policy",
        "controlflowspec": "control_flow",
        "llmroutingspec": "llm_routing",
    }
    expected_prefix = dim_prefix.get(type_name, "")
    if expected_prefix and parts[0] == expected_prefix and len(parts) > 1:
        parts = parts[1:]

    for part in parts[:-1]:
        if isinstance(obj, dict):
            obj = obj[part]
        else:
            obj = getattr(obj, part)
    return obj, parts[-1]


def _apply_field_edit(obj: Any, field: str, edit: HarnessEdit) -> None:
    """Apply an edit to a field on an object."""
    current = getattr(obj, field) if not isinstance(obj, dict) else obj.get(field)

    if edit.operation == EditOperation.REPLACE or edit.operation == EditOperation.SET:
        if isinstance(obj, dict):
            obj[field] = edit.new_value
        else:
            setattr(obj, field, edit.new_value)

    elif edit.operation == EditOperation.APPEND:
        if not isinstance(current, list):
            raise ValueError(f"Cannot APPEND to non-list field {field} (type: {type(current).__name__})")
        current.append(edit.new_value)
        if isinstance(obj, dict):
            obj[field] = current
        else:
            setattr(obj, field, current)

    elif edit.operation == EditOperation.REMOVE:
        if not isinstance(current, list):
            raise ValueError(f"Cannot REMOVE from non-list field {field}")
        if edit.old_value in current:
            current.remove(edit.old_value)
            if isinstance(obj, dict):
                obj[field] = current
            else:
                setattr(obj, field, current)

    elif edit.operation == EditOperation.ADJUST:
        if not isinstance(current, (int, float)):
            raise ValueError(f"Cannot ADJUST non-numeric field {field}")
        if isinstance(edit.new_value, (int, float)):
            if isinstance(obj, dict):
                obj[field] = current * edit.new_value
            else:
                setattr(obj, field, current * edit.new_value)
        else:
            raise ValueError(f"ADJUST requires numeric new_value, got {type(edit.new_value).__name__}")

    elif edit.operation == EditOperation.MERGE:
        if not isinstance(current, dict) or not isinstance(edit.new_value, dict):
            raise ValueError("MERGE requires dict values on both sides")
        merged = {**current, **edit.new_value}
        if isinstance(obj, dict):
            obj[field] = merged
        else:
            setattr(obj, field, merged)


def _apply_tool_edit(config: HarnessConfig, edit: HarnessEdit) -> None:
    """Apply a tool-registry edit (add/remove tool name from list)."""
    if edit.operation == EditOperation.APPEND:
        if edit.new_value not in config.tool_registry_names:
            config.tool_registry_names.append(edit.new_value)
    elif edit.operation == EditOperation.REMOVE:
        if edit.old_value in config.tool_registry_names:
            config.tool_registry_names.remove(edit.old_value)
    elif edit.operation in (EditOperation.REPLACE, EditOperation.SET):
        if isinstance(edit.new_value, list):
            config.tool_registry_names = list(edit.new_value)
        else:
            raise ValueError("TOOL REPLACE/SET requires list new_value")


# ---------------------------------------------------------------------------
# Harness Config Store — persistent configs for evolution tracking
# ---------------------------------------------------------------------------

class HarnessConfigStore:
    """Persist and retrieve HarnessConfigs by ID. Backed by JSON files in .jambu/harness/.

    This enables the evolution loop to:
    - Look up the config that produced a particular run
    - Compare configs across evolution rounds
    - Store configs for ensemble variant isolation
    """

    def __init__(self, base_dir: Optional[str] = None):
        import os
        from pathlib import Path
        self._dir = Path(base_dir or os.path.expanduser("~/.jambu/harness/configs"))
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, config: HarnessConfig) -> str:
        """Persist a config to disk. Returns config_id."""
        path = self._dir / f"{config.config_id}.json"
        path.write_text(config.to_json())
        # Also update the "latest" pointer per tag
        for tag in config.tags:
            latest = self._dir / f"latest_{tag}.json"
            latest.write_text(config.config_id)
        latest_all = self._dir / "latest.json"
        latest_all.write_text(config.config_id)
        return config.config_id

    def load(self, config_id: str) -> Optional[HarnessConfig]:
        """Load a config by ID. Returns None if not found."""
        path = self._dir / f"{config_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return _dict_to_config(data)

    def load_latest(self, tag: Optional[str] = None) -> Optional[HarnessConfig]:
        """Load the most recent config, optionally filtered by tag."""
        if tag:
            latest = self._dir / f"latest_{tag}.json"
        else:
            latest = self._dir / "latest.json"
        if not latest.exists():
            return None
        config_id = latest.read_text().strip()
        return self.load(config_id)

    def list_configs(self, tag: Optional[str] = None, limit: int = 50) -> list[HarnessConfig]:
        """List persisted configs, newest first, optionally filtered by tag."""
        configs: list[HarnessConfig] = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.name.startswith("latest"):
                continue
            try:
                config = self.load(path.stem)
                if config:
                    if tag and tag not in config.tags:
                        continue
                    configs.append(config)
            except Exception:
                pass
            if len(configs) >= limit:
                break
        return configs


def _dict_to_config(d: dict) -> HarnessConfig:
    """Reconstruct a HarnessConfig from a dict."""
    return HarnessConfig(
        config_id=d.get("config_id", ""),
        version=d.get("version", 1),
        parent_id=d.get("parent_id"),
        created_at=d.get("created_at", time.time()),
        description=d.get("description", ""),
        prompts=PromptConfig(**d.get("prompts", {})),
        tool_registry_names=d.get("tool_registry_names", []),
        memory_policy=MemoryPolicy(**d.get("memory_policy", {})),
        control_flow=ControlFlowSpec(**d.get("control_flow", {})),
        llm_routing=LLMRoutingSpec(**d.get("llm_routing", {})),
        evolution_round=d.get("evolution_round", 0),
        success_rate=d.get("success_rate"),
        tags=d.get("tags", []),
    )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_CONFIG_STORE: Optional[HarnessConfigStore] = None


def get_config_store() -> HarnessConfigStore:
    global _CONFIG_STORE
    if _CONFIG_STORE is None:
        _CONFIG_STORE = HarnessConfigStore()
    return _CONFIG_STORE
