"""
ReAct / Plan-Execute Agent Loop
================================

A proper agent loop that the research and tool-using endpoints route through.
Replaces the fixed linear pipeline in `/research` with:

1. **Plan** — decompose the user's goal into ordered steps
2. **Step** — select a tool, call it, observe the result
3. **Verify** — did this step advance the goal? (LLM judges)
4. **Replan** — if not, ask the LLM to revise the plan
5. **Synthesize** — combine observations into a final answer

HarnessX Integration (Phases 1-3)
---------------------------------
The agent now supports HarnessConfig-driven execution, enabling the AEGIS
evolution pipeline to produce config variants and run them through the agent
without code changes. See `harness.py` for the type definitions and
`evolution.py` for the full AEGIS pipeline.

Public API
----------
- `Agent`              — main loop class (now accepts HarnessConfig)
- `run_agent(query, ...)` — one-shot helper
- `Tool`, `ToolSpec`, `ToolRegistry` — tool definitions
- `Plan`, `Step`, `StepVerdict` — plan structure
- `AgentEvent`         — SSE event types
- `HarnessConfig`      — typed harness configuration (HarnessX Phase 1)
- `HarnessEdit`        — typed harness edit (substitution algebra)
- `Digester`           — failure clustering from agent run history (Phase 2)
- `Planner`, `Evolver`, `Critic`, `EnsembleRunner`, `EvolutionLoop` — AEGIS pipeline (Phases 2-3)
"""

from .loop import Agent, run_agent, AgentRunResult
from .plan import Plan, StepStatus, PlanStep, decompose_goal
from .tools import Tool, ToolSpec, ToolRegistry, ToolResult, get_registry as get_tool_registry
from .verifier import StepVerdict, verify_step
from .events import AgentEvent, EventType
from .builtin_tools import register_builtin_tools

# HarnessX types (Phase 1)
from .harness import (
    HarnessConfig,
    HarnessEdit,
    EditDimension,
    EditOperation,
    MemoryPolicy,
    ControlFlowSpec,
    LLMRoutingSpec,
    PromptConfig,
    HarnessConfigStore,
    get_config_store,
    apply_edit,
    apply_edits,
    revert_edit,
    diff_configs,
)
from .harness_defaults import (
    get_preset,
    list_presets,
    build_config,
)

# AEGIS pipeline (Phases 2-3) — optional imports
try:
    from .digester import Digester, FailureCluster  # noqa: F401
except ImportError:
    Digester = None  # type: ignore
    FailureCluster = None  # type: ignore

try:
    from .evolution import (  # noqa: F401
        Planner,
        Evolver,
        Critic,
        EnsembleRunner,
        EvolutionLoop,
        EvolutionResult,
        RoundResult,
    )
except ImportError:
    Planner = None  # type: ignore
    Evolver = None  # type: ignore
    Critic = None  # type: ignore
    EnsembleRunner = None  # type: ignore
    EvolutionLoop = None  # type: ignore
    EvolutionResult = None  # type: ignore
    RoundResult = None  # type: ignore

# Co-Evolution (Phase 4) — optional imports
try:
    from .coevolution import (  # noqa: F401
        MixedPolicyBuffer,
        GRPOTrainer,
        CoEvolutionLoop,
        CoEvolutionResult,
        CoEvolutionRound,
        Trajectory,
        TrainingExample,
    )
except ImportError:
    MixedPolicyBuffer = None  # type: ignore
    GRPOTrainer = None  # type: ignore
    CoEvolutionLoop = None  # type: ignore
    CoEvolutionResult = None  # type: ignore
    CoEvolutionRound = None  # type: ignore
    Trajectory = None  # type: ignore
    TrainingExample = None  # type: ignore

__all__ = [
    # Core agent
    "Agent",
    "run_agent",
    "AgentRunResult",
    "Plan",
    "StepStatus",
    "PlanStep",
    "decompose_goal",
    "Tool",
    "ToolSpec",
    "ToolRegistry",
    "ToolResult",
    "get_tool_registry",
    "StepVerdict",
    "verify_step",
    "AgentEvent",
    "EventType",
    "register_builtin_tools",
    # HarnessX types (Phase 1)
    "HarnessConfig",
    "HarnessEdit",
    "EditDimension",
    "EditOperation",
    "MemoryPolicy",
    "ControlFlowSpec",
    "LLMRoutingSpec",
    "PromptConfig",
    "HarnessConfigStore",
    "get_config_store",
    "apply_edit",
    "apply_edits",
    "revert_edit",
    "diff_configs",
    "get_preset",
    "list_presets",
    "build_config",
    # AEGIS pipeline (Phases 2-3)
    "Digester",
    "FailureCluster",
    "Planner",
    "Evolver",
    "Critic",
    "EnsembleRunner",
    "EvolutionLoop",
    "EvolutionResult",
    "RoundResult",
    # Co-Evolution (Phase 4)
    "MixedPolicyBuffer",
    "GRPOTrainer",
    "CoEvolutionLoop",
    "CoEvolutionResult",
    "CoEvolutionRound",
    "Trajectory",
    "TrainingExample",
]
