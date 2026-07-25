"""
The main ReAct/Plan-Execute agent loop.

Algorithm
---------
```
for step in plan:
    execute tool
    verify outcome
    if not advanced:
        replan
        continue
```

Streams events as it runs so the frontend gets a live view. The loop is
budget-aware (max_steps, max_tokens, max_seconds) and supports tool use via
the LLM's native tool-use API (Anthropic or OpenAI).

The Agent now accepts an optional HarnessConfig for dependency-injected
harness configuration. When provided, all loop parameters (budget, prompts,
memory policy, LLM routing, verification) are driven from the config rather
than hardcoded defaults. This enables the AEGIS evolution pipeline to
produce config variants and run them through the agent without code changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from backend.llm import ChatMessage, Role, Usage, get_default

from .events import (
    AgentEvent,
    EventType,
    answer_ready,
    log_event,
    plan_created,
    replanned,
    run_completed,
    run_failed,
    run_started,
    step_started,
    step_verified,
    tool_called,
    tool_failed,
)
from .plan import Plan, PlanStep, StepStatus, decompose_goal, replan
from .tools import ToolRegistry, get_registry as get_tool_registry
from .verifier import StepVerdict, verify_step
from .builtin_tools import _teardown_browser

# Optional harness config import — only used when config-driven mode is active
try:
    from .harness import HarnessConfig, MemoryPolicy, ControlFlowSpec, LLMRoutingSpec, PromptConfig  # noqa: F401
except ImportError:
    HarnessConfig = None  # type: ignore

log = logging.getLogger("jambu.agent.loop")


@dataclass
class AgentRunResult:
    """The non-streaming result of an agent run."""
    run_id: str
    query: str
    answer: str
    plan: Plan
    steps_executed: int
    sources: list[str] = field(default_factory=list)
    total_usage: Usage = field(default_factory=Usage)
    duration_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "query": self.query,
            "answer": self.answer,
            "plan": self.plan.to_dict(),
            "steps_executed": self.steps_executed,
            "sources": self.sources,
            "usage": {
                "prompt_tokens": self.total_usage.prompt_tokens,
                "completion_tokens": self.total_usage.completion_tokens,
                "total_tokens": self.total_usage.total_tokens,
                "cost_usd": self.total_usage.cost_usd,
            },
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error": self.error,
        }


class Agent:
    """The ReAct/Plan-Execute loop.

    Supports two modes:
    1. **Explicit params** — max_steps, max_tokens, max_seconds passed directly
       (backward compatible with existing callers).
    2. **HarnessConfig-driven** — all parameters read from an injected
       HarnessConfig (enables AEGIS evolution pipeline).

    When a HarnessConfig is provided, it takes precedence over explicit params.
    """

    def __init__(
        self,
        *,
        tool_registry: Optional[ToolRegistry] = None,
        max_steps: int = 10,
        max_tokens: int = 30000,
        max_seconds: float = 120.0,
        auto_register_builtins: bool = True,
        harness_config: Optional["HarnessConfig"] = None,  # type: ignore
    ):
        self.tools = tool_registry or get_tool_registry()
        if auto_register_builtins:
            from .builtin_tools import register_builtin_tools
            register_builtin_tools(self.tools)

        # Store the harness config for AEGIS trace correlation
        self.harness_config = harness_config

        # Resolve parameters: config-driven if available, otherwise explicit
        if harness_config is not None:
            cf = harness_config.control_flow
            self.max_steps = cf.max_steps
            self.max_tokens = cf.max_tokens
            self.max_seconds = cf.max_seconds
            self._prompts = harness_config.prompts
            self._memory_policy = harness_config.memory_policy
            self._llm_routing = harness_config.llm_routing
        else:
            self.max_steps = max_steps
            self.max_tokens = max_tokens
            self.max_seconds = max_seconds
            self._prompts = None
            self._memory_policy = None
            self._llm_routing = None

        self._run_history: list[AgentRunResult] = []

    @property
    def config_id(self) -> str:
        """Return the harness config ID if config-driven, else empty string."""
        return self.harness_config.config_id if self.harness_config else ""

    @property
    def is_config_driven(self) -> bool:
        """True if the agent is using an injected HarnessConfig."""
        return self.harness_config is not None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def run(
        self,
        query: str,
        *,
        user_id: str = "default",
        context: str = "",
        run_id: Optional[str] = None,
        max_steps: Optional[int] = None,
        max_tokens: Optional[int] = None,
        max_seconds: Optional[float] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the agent loop, yielding events as it goes.

        Per-run budget overrides (max_steps/max_tokens/max_seconds) apply to
        THIS run only; when omitted, the instance defaults are used. They are
        resolved into locals so concurrent runs on a shared Agent instance
        never mutate each other's budgets.
        """
        run_id = run_id or uuid.uuid4().hex[:12]
        max_steps = self.max_steps if max_steps is None else max_steps
        max_tokens = self.max_tokens if max_tokens is None else max_tokens
        max_seconds = self.max_seconds if max_seconds is None else max_seconds
        started = time.monotonic()
        total_usage = Usage()
        steps_executed = 0
        sources: list[str] = []
        answer_text = ""
        plan = Plan()

        yield run_started(run_id, query, user_id)

        # Consult procedural memory so the planner sees past approaches
        # (which worked, which didn't) and starts from a warm cache.
        if user_id:
            try:
                from backend.memory.retrieval import get_procedural_hints
                hints = get_procedural_hints(user_id, query)
                if hints:
                    context = (context + "\n" + hints) if context else hints
            except Exception:
                pass  # procedural memory is advisory; never block plan generation

        # Step 0: Decompose goal into a plan
        try:
            plan = await decompose_goal(
                query,
                available_tools=self.tools.list_names(),
                user_context=context,
                max_steps=max_steps,
                prompt_template=(
                    self._prompts.planner_user_template if self._prompts else None
                ),
            )
            yield plan_created(run_id, plan.to_dict())
        except Exception as e:
            log.exception("Plan decomposition failed")
            yield run_failed(run_id, f"plan_decomposition_failed: {e}")
            return

        # Step 1..N: Execute the plan
        answer_produced = False
        for step_idx, step in enumerate(plan.steps):
            if steps_executed >= max_steps:
                yield log_event(run_id, "warn", f"max_steps={max_steps} reached")
                break
            elapsed = time.monotonic() - started
            if elapsed > max_seconds:
                yield log_event(run_id, "warn", f"max_seconds={max_seconds} exceeded")
                break
            if total_usage.total_tokens >= max_tokens:
                yield log_event(run_id, "warn", f"max_tokens={max_tokens} reached")
                break

            step.status = StepStatus.RUNNING
            yield step_started(run_id, step.to_dict())

            if step.tool is None:
                # Reasoning step with no tool — treat as success and continue
                step.status = StepStatus.SUCCEEDED
                steps_executed += 1
                yield step_verified(run_id, step.to_dict(), StepVerdict(advanced=True, confidence=1.0, feedback="reasoning step").to_dict())
                continue

            # Execute the tool
            try:
                tool_result = await self.tools.execute(step.tool, **step.args)
            except Exception as e:
                log.exception("Tool %s raised", step.tool)
                tool_result = None
                yield tool_failed(run_id, step.tool, step.args, str(e))
                step.status = StepStatus.FAILED
                step.error = str(e)
                # Replan
                new_plan = await replan(
                    query,
                    step,
                    {"error": str(e)},
                    available_tools=self.tools.list_names(),
                    max_steps=max_steps - steps_executed,
                    prompt_template=(
                        self._prompts.replanner_user_template if self._prompts else None
                    ),
                )
                plan = new_plan
                yield replanned(run_id, f"step_failed: {e}", plan.to_dict())
                continue

            if not tool_result.success:
                yield tool_failed(run_id, step.tool, step.args, tool_result.error or "unknown error")
                step.status = StepStatus.FAILED
                step.error = tool_result.error
                # Replan
                new_plan = await replan(
                    query,
                    step,
                    {"error": tool_result.error},
                    available_tools=self.tools.list_names(),
                    max_steps=max_steps - steps_executed,
                    prompt_template=(
                        self._prompts.replanner_user_template if self._prompts else None
                    ),
                )
                plan = new_plan
                yield replanned(run_id, f"step_failed: {tool_result.error}", plan.to_dict())
                continue

            step.status = StepStatus.SUCCEEDED
            step.result = tool_result.to_dict()
            yield tool_called(run_id, step.tool, step.args, tool_result.to_dict())
            steps_executed += 1
            total_usage = total_usage + Usage(  # rough attribution
                prompt_tokens=int(tool_result.metadata.get("prompt_tokens", 0) or 0),
                completion_tokens=int(tool_result.metadata.get("completion_tokens", 0) or 0),
            )

            # Collect sources from the result
            for k in ("url", "source", "link"):
                if k in tool_result.data and isinstance(tool_result.data[k], str):
                    sources.append(tool_result.data[k])
            if isinstance(tool_result.data, dict):
                for r in tool_result.data.get("results", []) or []:
                    if isinstance(r, dict) and "url" in r:
                        sources.append(r["url"])

            # Verify
            remaining = [s for s in plan.steps[step_idx + 1:] if s.status == StepStatus.PENDING]
            verdict = await verify_step(
                query, step, tool_result.to_dict(), remaining,
                prompt_template=(
                    self._prompts.verifier_user_template if self._prompts else None
                ),
            )
            step.verification = verdict.to_dict()
            yield step_verified(run_id, step.to_dict(), verdict.to_dict())

            # Use config-driven replan threshold if available
            cf_threshold = (
                self.harness_config.control_flow.replan_confidence_threshold
                if self.harness_config
                else 0.7
            )
            auto_replan = (
                self.harness_config.control_flow.replan_on_weak_progress
                if self.harness_config
                else True
            )

            if not verdict.advanced and verdict.confidence >= cf_threshold and auto_replan:
                # LLM said the step didn't advance — replan
                new_plan = await replan(
                    query, step, verdict.to_dict(),
                    available_tools=self.tools.list_names(),
                    max_steps=max_steps - steps_executed,
                    prompt_template=(
                        self._prompts.replanner_user_template if self._prompts else None
                    ),
                )
                plan = new_plan
                yield replanned(run_id, verdict.feedback or "verification_rejected", plan.to_dict())
                continue

            # Check if this is the final answer
            if step.tool == "final_answer":
                data = tool_result.data or {}
                answer_text = data.get("text", "") if isinstance(data, dict) else str(data)
                if isinstance(data, dict) and data.get("sources"):
                    sources.extend(data["sources"])
                answer_produced = True
                break

        # If no final_answer step, synthesize from observations
        if not answer_produced:
            answer_text = await self._synthesize(query, plan, total_usage)
            total_usage = total_usage + Usage(
                prompt_tokens=int(getattr(self, "_last_synth_usage", Usage()).prompt_tokens),
                completion_tokens=int(getattr(self, "_last_synth_usage", Usage()).completion_tokens),
            )

        # Final event
        duration = (time.monotonic() - started) * 1000
        unique_sources = list(dict.fromkeys(sources))[:20]  # dedupe + cap

        # Cache for non-streaming result — append BEFORE yielding so consumers
        # can read the result on the run_completed event.
        result = AgentRunResult(
            run_id=run_id,
            query=query,
            answer=answer_text,
            plan=plan,
            steps_executed=steps_executed,
            sources=unique_sources,
            total_usage=total_usage,
            duration_ms=duration,
            success=True,
        )
        self._run_history.append(result)

        await _teardown_browser()

        yield answer_ready(run_id, answer_text, unique_sources, total_usage.__dict__)
        yield run_completed(run_id, duration, steps_executed, total_usage.total_tokens, total_usage.cost_usd)

    async def run_to_completion(
        self,
        query: str,
        *,
        user_id: str = "default",
        context: str = "",
        run_id: Optional[str] = None,
        max_steps: Optional[int] = None,
        max_tokens: Optional[int] = None,
        max_seconds: Optional[float] = None,
    ) -> AgentRunResult:
        """Run to completion, returning the final AgentRunResult.

        Budget overrides are forwarded to run() and apply to this run only.
        """
        result: Optional[AgentRunResult] = None
        async for event in self.run(
            query,
            user_id=user_id,
            context=context,
            run_id=run_id,
            max_steps=max_steps,
            max_tokens=max_tokens,
            max_seconds=max_seconds,
        ):
            if event.type == EventType.RUN_COMPLETED:
                # Get the most recent result
                if self._run_history:
                    result = self._run_history[-1]
        if result is None:
            result = AgentRunResult(
                run_id=run_id or "?",
                query=query,
                answer="(no result)",
                plan=Plan(),
                steps_executed=0,
                success=False,
                error="run did not complete",
            )
        return result

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _synthesize(self, query: str, plan: Plan, total_usage: Usage) -> str:
        """Synthesize a final answer from the plan's observations when no
        explicit final_answer step was provided."""
        obs_parts: list[str] = []
        for s in plan.steps:
            if s.result and s.status == StepStatus.SUCCEEDED:
                data = s.result.get("data")
                if isinstance(data, dict):
                    obs_parts.append(json.dumps(data)[:1500])
                else:
                    obs_parts.append(str(data)[:1500])
        observations = "\n\n".join(obs_parts) or "(no tool observations)"

        # Use config-driven synthesis prompt if available, else hardcoded default
        if self._prompts and self._prompts.synthesis_user_template:
            prompt = self._prompts.synthesis_user_template.format(
                query=query,
                observations=observations,
            )
        else:
            prompt = (
                f"User asked: {query}\n\n"
                f"Tool observations:\n{observations}\n\n"
                "Based on the observations, write a clear final answer to the user. "
                "Cite specific sources if any URLs were collected. Be concise."
            )

        # Use config-driven synthesis max_tokens if available
        synth_max_tokens = (
            self.harness_config.control_flow.synthesis_max_tokens
            if self.harness_config
            else 800
        )
        synth_temp = (
            self.harness_config.control_flow.synthesis_temperature
            if self.harness_config
            else 0.3
        )

        try:
            llm = get_default()
            resp = await llm.chat(
                [ChatMessage(role=Role.USER, content=prompt)],
                temperature=synth_temp,
                max_tokens=synth_max_tokens,
            )
            self._last_synth_usage = resp.usage
            return resp.content or "(synthesis produced no text)"
        except Exception as e:
            return f"(synthesis failed: {e})"

    @property
    def history(self) -> list[AgentRunResult]:
        return list(self._run_history)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

async def run_agent(
    query: str,
    *,
    user_id: str = "default",
    context: str = "",
    max_steps: int = 10,
    max_tokens: int = 30000,
    max_seconds: float = 120.0,
) -> AsyncIterator[AgentEvent]:
    """One-shot agent run. Yields events."""
    agent = Agent(max_steps=max_steps, max_tokens=max_tokens, max_seconds=max_seconds)
    async for event in agent.run(query, user_id=user_id, context=context):
        yield event


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_agent_instance: Optional["Agent"] = None


def get_agent() -> "Agent":
    """Return the process-wide Agent singleton.

    The singleton owns the in-memory ``_run_history`` list, so consecutive
    requests via ``/v2/agent/run`` accumulate history that ``/v2/agent/history``
    can read. Per-request budgets (max_steps/max_tokens/max_seconds) must be
    passed as keyword arguments to ``run()`` / ``run_to_completion()`` — they
    apply to that run only. Do NOT mutate ``agent.max_steps`` etc. on the
    singleton: that races concurrent requests and leaks one request's budget
    into the next.
    """
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = Agent()
    return _agent_instance


def reset_agent_singleton() -> None:
    """Drop the cached agent (for tests)."""
    global _agent_instance
    _agent_instance = None
