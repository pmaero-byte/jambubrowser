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



def enrich_context(query: str, user_id: Optional[str]) -> str:
    """Add what previous runs learned to the planner's context.

    Two advisory sources, both optional and both best-effort:

    * **Procedural memory** — what worked and what did not for this user, so a
      planner does not re-try an approach that already failed.
    * **The plan library** — the plan template that succeeded for a near
      identical goal. The planner still inspects the live page; this only
      biases where it starts.

    Both are looked up lazily and swallowed on failure, because a missing memory
    table must not stop an agent from running.
    """
    parts: list[str] = []
    if user_id:
        try:
            from backend.memory.retrieval import get_procedural_hints

            hints = get_procedural_hints(user_id, query)
            if hints:
                parts.append(hints)
        except Exception:
            log.debug("procedural memory hints unavailable", exc_info=True)
    try:
        from backend.agent.plan_library import advise_planner

        template = advise_planner(query)
        if template:
            parts.append(template)
    except Exception:
        log.debug("planner library advice unavailable", exc_info=True)
    return "\n\n".join(parts)


def budget_exhausted(
    *,
    steps_executed: int,
    total_tokens: int,
    elapsed_seconds: float,
    max_steps: int,
    max_tokens: int,
    max_seconds: float,
) -> Optional[str]:
    """Return the reason the run must stop, or None to keep going.

    Checked before every step rather than after, so a run can never start work
    it has no budget for. The message names the *budget*, not the tool, because
    the useful question when this fires is which knob to turn.
    """
    if steps_executed >= max_steps:
        return f"max_steps={max_steps} reached"
    if elapsed_seconds > max_seconds:
        return f"max_seconds={max_seconds} exceeded"
    if total_tokens >= max_tokens:
        return f"max_tokens={max_tokens} reached"
    return None


def collect_sources(tool_result, into: list[str]) -> None:
    """Append every source URL a tool result carries.

    Tools report provenance under three different keys and a `results` list, so
    all four shapes are read here. Order is preserved and de-duplication happens
    once at the end of the run.
    """
    data = tool_result.data
    if isinstance(data, dict):
        for key in ("url", "source", "link"):
            if isinstance(data.get(key), str):
                into.append(data[key])
        for row in data.get("results", []) or []:
            if isinstance(row, dict) and "url" in row:
                into.append(row["url"])


def extract_answer(tool_result) -> tuple[str, list[str]]:
    """Pull the final answer text and its sources out of a `final_answer` result."""
    data = tool_result.data or {}
    text = data.get("text", "") if isinstance(data, dict) else str(data)
    sources = list(data.get("sources", [])) if isinstance(data, dict) else []
    return text, sources

@dataclass
class StepOutcome:
    """What one plan step produced, filled by `_execute_step`.

    Python async generators cannot return a value, so the state a step produces
    comes back through this record rather than a return value. It is also what
    makes the three exits from a step — tool raised, tool failed, tool succeeded
    — legible at the call site: read one field instead of tracking flags.
    """

    #: A replacement plan, when the step triggered a replan.
    plan: Optional[Plan] = None
    #: Tokens the tool reported, for run-total attribution.
    usage: Usage = field(default_factory=Usage)
    #: Sources the tool result carried.
    sources: list[str] = field(default_factory=list)
    #: Set when this step produced the run's answer.
    answer_text: str = ""
    answer_produced: bool = False
    #: Whether the step counted against max_steps.
    counted: bool = False


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

        The body is the sequence, not the mechanics: enrich the planner's
        context, decompose, execute steps until the budget or an answer stops
        it, then report. Everything step-sized lives in `_execute_step`, which
        yields the same events in the same order as the inline version did.
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

        enriched = enrich_context(query, user_id)
        if enriched:
            context = f"{context}\n{enriched}" if context else enriched

        # Step 0: Decompose goal into a plan. There is no fallback here — a run
        # without a plan has nothing to execute, and pretending otherwise would
        # hide the failure behind a synthesized answer.
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

        # Step 1..N: Execute the plan.
        answer_produced = False
        for step_idx, step in enumerate(plan.steps):
            stopped = budget_exhausted(
                steps_executed=steps_executed,
                total_tokens=total_usage.total_tokens,
                elapsed_seconds=time.monotonic() - started,
                max_steps=max_steps,
                max_tokens=max_tokens,
                max_seconds=max_seconds,
            )
            if stopped:
                yield log_event(run_id, "warn", stopped)
                break

            outcome = StepOutcome()
            remaining = [
                s for s in plan.steps[step_idx + 1:] if s.status == StepStatus.PENDING
            ]
            async for event in self._execute_step(
                step,
                query=query,
                run_id=run_id,
                remaining=remaining,
                max_steps_left=max_steps - steps_executed,
                outcome=outcome,
            ):
                yield event

            if outcome.plan is not None:
                plan = outcome.plan
            if outcome.counted:
                steps_executed += 1
            total_usage = total_usage + outcome.usage
            sources.extend(outcome.sources)
            if outcome.answer_produced:
                answer_text = outcome.answer_text
                answer_produced = True
                break

        # If no final_answer step ran, synthesize an answer from what was
        # observed rather than reporting nothing.
        if not answer_produced:
            answer_text = await self._synthesize(query, plan, total_usage)
            total_usage = total_usage + Usage(
                prompt_tokens=int(getattr(self, "_last_synth_usage", Usage()).prompt_tokens),
                completion_tokens=int(getattr(self, "_last_synth_usage", Usage()).completion_tokens),
            )

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
        self._cache_plan_template(query, plan)

        await _teardown_browser()

        yield answer_ready(run_id, answer_text, unique_sources, total_usage.__dict__)
        yield run_completed(run_id, duration, steps_executed, total_usage.total_tokens, total_usage.cost_usd)

    async def _execute_step(
        self,
        step: PlanStep,
        *,
        query: str,
        run_id: str,
        remaining: list[PlanStep],
        max_steps_left: int,
        outcome: StepOutcome,
    ) -> AsyncIterator[AgentEvent]:
        """Execute, verify and possibly replan one plan step.

        Fills ``outcome`` with everything the run loop needs to carry forward:
        a replacement plan, token usage, sources, and whether this step produced
        the answer. Three exits, in the order they are checked:

        1. **Reasoning step** (no tool) — counts as a step and passes
           verification with full confidence. The agent is allowed to think.
        2. **The tool raised or reported failure** — the failure is recorded,
           the step is marked failed, and the loop replans from the error.
        3. **The tool succeeded** — the result is verified, and a verdict that
           says the step did not advance triggers a replan too.

        Replanning is the same in both failure paths and in the weak-progress
        case, which is why it is one method (`_replan_after`) called three times
        rather than three copies.
        """
        step.status = StepStatus.RUNNING
        yield step_started(run_id, step.to_dict())

        # 1. Reasoning step: no tool to call.
        if step.tool is None:
            step.status = StepStatus.SUCCEEDED
            outcome.counted = True
            yield step_verified(
                run_id,
                step.to_dict(),
                StepVerdict(
                    advanced=True, confidence=1.0, feedback="reasoning step",
                ).to_dict(),
            )
            return

        # 2a. The tool raised.
        try:
            tool_result = await self.tools.execute(step.tool, **step.args)
        except Exception as e:
            log.exception("Tool %s raised", step.tool)
            yield tool_failed(run_id, step.tool, step.args, str(e))
            step.status = StepStatus.FAILED
            step.error = str(e)
            async for event in self._replan_after(
                query, step, {"error": str(e)},
                run_id=run_id, max_steps_left=max_steps_left, outcome=outcome,
                reason=f"step_failed: {e}",
            ):
                yield event
            return

        # 2b. The tool ran but reported failure.
        if not tool_result.success:
            yield tool_failed(
                run_id, step.tool, step.args, tool_result.error or "unknown error",
            )
            step.status = StepStatus.FAILED
            step.error = tool_result.error
            async for event in self._replan_after(
                query, step, {"error": tool_result.error},
                run_id=run_id, max_steps_left=max_steps_left, outcome=outcome,
                reason=f"step_failed: {tool_result.error}",
            ):
                yield event
            return

        # 3. The tool succeeded.
        step.status = StepStatus.SUCCEEDED
        step.result = tool_result.to_dict()
        yield tool_called(run_id, step.tool, step.args, tool_result.to_dict())
        outcome.counted = True
        outcome.usage = Usage(  # rough attribution from whatever the tool reported
            prompt_tokens=int(tool_result.metadata.get("prompt_tokens", 0) or 0),
            completion_tokens=int(tool_result.metadata.get("completion_tokens", 0) or 0),
        )
        collect_sources(tool_result, outcome.sources)

        verdict = await verify_step(
            query, step, tool_result.to_dict(), remaining,
            prompt_template=(
                self._prompts.verifier_user_template if self._prompts else None
            ),
        )
        step.verification = verdict.to_dict()
        yield step_verified(run_id, step.to_dict(), verdict.to_dict())

        # A verdict that says the step did not advance replans the rest of the
        # run — but only when the harness says to. Both the threshold and the
        # on/off switch are configuration, with the historical values as the
        # default when no harness config is loaded.
        cf = self.harness_config.control_flow if self.harness_config else None
        cf_threshold = cf.replan_confidence_threshold if cf else 0.7
        auto_replan = cf.replan_on_weak_progress if cf else True

        if not verdict.advanced and verdict.confidence >= cf_threshold and auto_replan:
            async for event in self._replan_after(
                query, step, verdict.to_dict(),
                run_id=run_id, max_steps_left=max_steps_left, outcome=outcome,
                reason=verdict.feedback or "verification_rejected",
            ):
                yield event
            return

        if step.tool == "final_answer":
            text, extra = extract_answer(tool_result)
            outcome.answer_text = text
            outcome.sources.extend(extra)
            outcome.answer_produced = True

    async def _replan_after(
        self,
        query: str,
        step: PlanStep,
        evidence: dict,
        *,
        run_id: str,
        max_steps_left: int,
        outcome: StepOutcome,
        reason: str,
    ) -> AsyncIterator[AgentEvent]:
        """Ask the planner for a new plan after a step failed or stalled.

        The replacement lands in ``outcome.plan``; the caller adopts it and keeps
        iterating. Emitting the `replanned` event here rather than at the call
        site is what keeps the three failure paths identical in what the consumer
        sees — an agent watching the stream cannot tell a tool crash from a
        rejected step.
        """
        outcome.plan = await replan(
            query,
            step,
            evidence,
            available_tools=self.tools.list_names(),
            max_steps=max_steps_left,
            prompt_template=(
                self._prompts.replanner_user_template if self._prompts else None
            ),
        )
        yield replanned(run_id, reason, outcome.plan.to_dict())

    def _cache_plan_template(self, query: str, plan: Plan) -> None:
        """Store this run's plan as a template for a similar future goal.

        Only ever called for a run that reached an answer, and deliberately
        advisory: a failure here must not disturb the completed run's events,
        which the caller has already decided to emit.
        """
        try:
            from backend.agent.plan_library import get_plan_library

            get_plan_library().put(query, plan.to_dict(), success=True)
        except Exception:
            log.debug("plan library write skipped", exc_info=True)

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
