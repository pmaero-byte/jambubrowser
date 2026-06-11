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

Public API
----------
- `Agent`              — main loop class
- `run_agent(query, ...)` — one-shot helper
- `Tool`, `ToolSpec`, `ToolRegistry` — tool definitions
- `Plan`, `Step`, `StepVerdict` — plan structure
- `AgentEvent`         — SSE event types
"""

from .loop import Agent, run_agent, AgentRunResult
from .plan import Plan, StepStatus, PlanStep, decompose_goal
from .tools import Tool, ToolSpec, ToolRegistry, ToolResult, get_registry as get_tool_registry
from .verifier import StepVerdict, verify_step
from .events import AgentEvent, EventType
from .builtin_tools import register_builtin_tools

__all__ = [
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
]
