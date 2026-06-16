"""
Plan generation — break a user goal into ordered, tool-callable steps.

The agent loop uses this to produce a `Plan` from the user's query. The
LLM sees the available tools and produces a list of steps, each describing
which tool to call with which args. The loop then executes them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.llm import ChatMessage, Role, get_default


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """A single step in a plan."""
    index: int
    description: str
    tool: Optional[str] = None  # if None, this is a reasoning step
    args: dict = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    result: Optional[dict] = None
    error: Optional[str] = None
    verification: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "description": self.description,
            "tool": self.tool,
            "args": self.args,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "verification": self.verification,
        }


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    raw_response: str = ""

    def is_empty(self) -> bool:
        return len(self.steps) == 0

    def next_pending(self) -> Optional[PlanStep]:
        for s in self.steps:
            if s.status == StepStatus.PENDING:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "raw": self.raw_response[:500] if self.raw_response else "",
        }


# ---------------------------------------------------------------------------
# Decomposition
# ---------------------------------------------------------------------------

DECOMPOSE_PROMPT = """You are a planning module for an AI research agent. Given a user's goal and a list of available tools, produce a step-by-step plan to achieve the goal.

Available tools:
{tool_descriptions}

User goal: {query}

{user_context}

Respond with a JSON object (no markdown fencing) of the form:
{{
  "steps": [
    {{"description": "<plain English>", "tool": "<tool_name or null>", "args": {{...}} }},
    ...
  ]
}}

Rules:
- 1 to {max_steps} steps, no more
- Each step must either call a tool OR be a final synthesis step (tool=null, args={{"text": "<final answer>"}})
- The last step's tool should typically be "final_answer" with text="<your final answer to the user>"
- Be concrete. If the goal requires research, include a "web_search" step first.
- If the goal is just a question, you can have a single "final_answer" step.
"""


def _describe_tools(tool_names: list[str]) -> str:
    """Build a one-line description per tool from the registry."""
    try:
        from .tools import get_tool_registry
        registry = get_tool_registry()
        lines = []
        for name in tool_names:
            if registry.has(name):
                spec = registry.get(name).spec
                params = ", ".join(spec.parameters.get("properties", {}).keys())
                lines.append(f"- {name}({params}): {spec.description}")
        return "\n".join(lines) if lines else "(no tools)"
    except Exception:
        return "(tools unavailable)"


async def decompose_goal(
    query: str,
    *,
    available_tools: list[str],
    user_context: str = "",
    max_steps: int = 8,
    prompt_template: Optional[str] = None,
) -> Plan:
    """Use the LLM to decompose a user goal into a Plan.

    Args:
        query: The user's goal/question.
        available_tools: Tool names the agent can use.
        user_context: Additional context (memories, profile).
        max_steps: Maximum number of plan steps.
        prompt_template: Optional override for DECOMPOSE_PROMPT. When provided,
                         must include {tool_descriptions}, {query}, {user_context},
                         {max_steps} format placeholders. Used by config-driven agents.
    """
    template = prompt_template or DECOMPOSE_PROMPT
    prompt = template.format(
        tool_descriptions=_describe_tools(available_tools),
        query=query,
        user_context=("User context:\n" + user_context) if user_context else "",
        max_steps=max_steps,
    )

    try:
        llm = get_default()
        from backend.llm import ChatMessage, Role
        resp = await llm.chat(
            [ChatMessage(role=Role.USER, content=prompt)],
            temperature=0.2,
            max_tokens=1500,
        )
        return _parse_plan_response(resp.content, raw=resp.content)
    except Exception as e:
        # Fallback: single-step plan that just synthesizes an apology
        return Plan(
            steps=[
                PlanStep(
                    index=0,
                    description="Acknowledge the planning failure and respond",
                    tool="final_answer",
                    args={"text": f"I was unable to plan a response: {e}. Could you rephrase your question?"},
                )
            ],
            raw_response=str(e),
        )


def _parse_plan_response(text: str, *, raw: str = "") -> Plan:
    """Parse the LLM's JSON plan response into a Plan object."""
    # Try to extract JSON
    text = text.strip()
    if text.startswith("```"):
        # Strip code fence
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    # Find the first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return Plan(raw_response=text)
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return Plan(raw_response=text)
    steps_raw = obj.get("steps", [])
    if not isinstance(steps_raw, list):
        return Plan(raw_response=text)
    plan = Plan(raw_response=text)
    for i, s in enumerate(steps_raw[:10]):  # cap at 10 steps
        if not isinstance(s, dict):
            continue
        plan.steps.append(PlanStep(
            index=i,
            description=str(s.get("description", "")),
            tool=s.get("tool"),
            args=s.get("args", {}) if isinstance(s.get("args"), dict) else {},
        ))
    return plan


# ---------------------------------------------------------------------------
# Replanning
# ---------------------------------------------------------------------------

REPLAN_PROMPT = """You are replanning after a failed step. Given:
- The original goal
- The plan so far with one step that failed
- A verdict explaining why it failed
Produce a revised plan (JSON, same format) that avoids the failure and still achieves the goal.

Goal: {query}
Failed step: {failed_step}
Failure: {verdict}

Available tools:
{tool_descriptions}

Respond with: {{"steps": [...]}}
"""


async def replan(
    query: str,
    failed_step: PlanStep,
    verdict: dict,
    *,
    available_tools: list[str],
    max_steps: int = 8,
    prompt_template: Optional[str] = None,
) -> Plan:
    """Ask the LLM to revise the plan after a step failure.

    Args:
        prompt_template: Optional override for REPLAN_PROMPT. When provided,
                         must include {query}, {failed_step}, {verdict},
                         {tool_descriptions} format placeholders.
    """
    template = prompt_template or REPLAN_PROMPT
    prompt = template.format(
        query=query,
        failed_step=failed_step.to_dict(),
        verdict=verdict,
        tool_descriptions=_describe_tools(available_tools),
    )
    try:
        from backend.llm import get_default, ChatMessage, Role
        llm = get_default()
        resp = await llm.chat(
            [ChatMessage(role=Role.USER, content=prompt)],
            temperature=0.2,
            max_tokens=1500,
        )
        return _parse_plan_response(resp.content, raw=resp.content)
    except Exception as e:
        # Last-resort: terminate with apology
        return Plan(steps=[
            PlanStep(
                index=0,
                description="Acknowledge the replan failure and respond",
                tool="final_answer",
                args={"text": f"I got stuck after a failed step: {e}. Please rephrase your request."},
            )
        ], raw_response=str(e))
