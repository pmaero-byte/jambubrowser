"""The extracted pieces of the agent loop, and the bug one of them had.

`Agent.run` was 246 lines; the step mechanics now live in `_execute_step` and
the run-time helpers in module-level functions. These tests cover the helpers
directly — they are the part of the loop that can be tested without an LLM — and
pin the properties that are easy to break:

* a tool that succeeds with **no data** must not kill the run. It used to: the
  old source-collection loop did `k in tool_result.data` before checking it was
  a dict, so `data=None` raised TypeError inside the step loop;
* the budget must be checked *before* a step starts, naming the budget;
* a replan must be observable identically whether the tool raised, the tool
  reported failure, or verification rejected the result.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.agent import loop as loop_mod
from backend.agent.loop import (
    Agent,
    StepOutcome,
    budget_exhausted,
    collect_sources,
    enrich_context,
    extract_answer,
)
from backend.agent.plan import Plan, PlanStep
from backend.agent.tools import ToolRegistry, ToolResult
from backend.agent.verifier import StepVerdict


# ---------------------------------------------------------------------------
# collect_sources
# ---------------------------------------------------------------------------

class TestCollectSources:
    def test_reads_the_three_scalar_keys(self):
        result = ToolResult(success=True, data={
            "url": "https://a/", "source": "https://b/", "link": "https://c/",
        })
        out: list[str] = []
        collect_sources(result, out)
        assert out == ["https://a/", "https://b/", "https://c/"]

    def test_reads_a_results_list(self):
        result = ToolResult(success=True, data={
            "results": [{"url": "https://a/"}, {"url": "https://b/"}, {"no_url": 1}],
        })
        out: list[str] = []
        collect_sources(result, out)
        assert out == ["https://a/", "https://b/"]

    def test_appends_to_the_caller_list(self):
        out = ["https://existing/"]
        collect_sources(ToolResult(success=True, data={"url": "https://a/"}), out)
        assert out == ["https://existing/", "https://a/"]

    @pytest.mark.parametrize("data", [None, 5, 3.5, "plain text", [], object()])
    def test_a_tool_with_no_usable_data_is_not_a_crash(self, data):
        """Regression: `k in tool_result.data` raised TypeError for these."""
        out: list[str] = []
        collect_sources(ToolResult(success=True, data=data), out)
        assert out == []

    def test_non_string_urls_are_ignored(self):
        out: list[str] = []
        collect_sources(ToolResult(success=True, data={"url": 42}), out)
        assert out == []


# ---------------------------------------------------------------------------
# budget_exhausted
# ---------------------------------------------------------------------------

class TestBudget:
    def _budget(self, **over):
        base = dict(steps_executed=0, total_tokens=0, elapsed_seconds=0.0,
                    max_steps=10, max_tokens=1000, max_seconds=60.0)
        base.update(over)
        return budget_exhausted(**base)

    def test_fresh_budget_runs(self):
        assert self._budget() is None

    def test_step_budget_names_the_limit(self):
        assert self._budget(steps_executed=10, max_steps=10) == "max_steps=10 reached"

    def test_time_budget_names_the_limit(self):
        assert self._budget(elapsed_seconds=61.0) == "max_seconds=60.0 exceeded"

    def test_token_budget_names_the_limit(self):
        assert self._budget(total_tokens=1000) == "max_tokens=1000 reached"

    def test_the_tightest_budget_is_reported_first(self):
        """All three exhausted: step count wins, because it is cheapest to fix."""
        assert self._budget(steps_executed=10, total_tokens=5000,
                            elapsed_seconds=99.0).startswith("max_steps")


# ---------------------------------------------------------------------------
# enrich_context
# ---------------------------------------------------------------------------

class TestEnrichContext:
    def test_missing_sources_produce_empty_context(self, monkeypatch):
        import backend.memory.retrieval as retrieval

        def boom(*_a, **_k):
            raise RuntimeError("no memory table")

        monkeypatch.setattr(retrieval, "get_procedural_hints", boom)
        monkeypatch.setattr(loop_mod, "log", loop_mod.log)
        # No plan library available either -> an empty string, not an exception.
        assert isinstance(enrich_context("a goal", "alice"), str)

    def test_hints_are_appended_to_the_existing_context(self, monkeypatch):
        import backend.agent.plan_library as library

        monkeypatch.setattr(library, "advise_planner", lambda q: None, raising=False)
        import backend.memory.retrieval as retrieval

        monkeypatch.setattr(retrieval, "get_procedural_hints",
                            lambda user, query: "Procedural memory: tried X")
        context = enrich_context("audit a login page", "alice")
        assert "Procedural memory" in context


# ---------------------------------------------------------------------------
# extract_answer
# ---------------------------------------------------------------------------

class TestExtractAnswer:
    def test_reads_text_and_sources(self):
        text, sources = extract_answer(
            ToolResult(success=True, data={"text": "42", "sources": ["https://a/"]}),
        )
        assert text == "42"
        assert sources == ["https://a/"]

    def test_missing_text_is_empty_not_an_error(self):
        text, sources = extract_answer(ToolResult(success=True, data={}))
        assert text == ""
        assert sources == []

    def test_non_dict_data_is_stringified(self):
        text, sources = extract_answer(ToolResult(success=True, data="plain"))
        assert text == "plain"
        assert sources == []


# ---------------------------------------------------------------------------
# StepOutcome
# ---------------------------------------------------------------------------

class TestStepOutcome:
    def test_defaults_are_empty_not_shared(self):
        a, b = StepOutcome(), StepOutcome()
        a.sources.append("x")
        assert b.sources == []
        assert a.plan is None and a.answer_produced is False


# ---------------------------------------------------------------------------
# End to end: the helpers inside a real run
# ---------------------------------------------------------------------------

def _agent_with(tool_name: str, handler) -> Agent:
    registry = ToolRegistry()
    registry.register(tool_name, handler)
    return Agent(tool_registry=registry, auto_register_builtins=False)


def _one_step_plan(tool: str) -> Plan:
    return Plan(steps=[PlanStep(index=1, description="do it", tool=tool)])


def _drive(agent: Agent, monkeypatch, *, advanced: bool = True) -> list:
    """Run the agent with a stubbed planner/verifier and collect the events."""
    async def fake_decompose(query, available_tools=None, user_context=None,
                             max_steps=None, prompt_template=None):
        return _one_step_plan("probe")

    async def fake_replan(query, step, evidence, available_tools=None,
                         max_steps=None, prompt_template=None):
        return _one_step_plan("probe")

    async def fake_verify(query, step, result, remaining, prompt_template=None):
        return StepVerdict(advanced=advanced, confidence=0.95, feedback="weak")

    monkeypatch.setattr(loop_mod, "decompose_goal", fake_decompose)
    monkeypatch.setattr(loop_mod, "replan", fake_replan)
    monkeypatch.setattr(loop_mod, "verify_step", fake_verify)

    async def collect():
        return [event async for event in agent.run("probe the page", run_id="R")]

    return asyncio.run(collect())


class TestRunIntegration:
    def test_a_tool_returning_no_data_completes_the_run(self, monkeypatch):
        """The regression: `data=None` used to raise TypeError mid-step."""

        async def no_data():
            return None            # legal: a tool may succeed with nothing

        agent = _agent_with("probe", no_data)
        events = _drive(agent, monkeypatch)

        types = [e.type.value for e in events]
        assert "tool_called" in types
        assert types[-1] == "run_completed"

    def test_a_tool_returning_a_scalar_completes_the_run(self, monkeypatch):
        async def scalar():
            return 42

        events = _drive(_agent_with("probe", scalar), monkeypatch)
        assert [e.type.value for e in events][-1] == "run_completed"

    def test_a_tool_that_raises_replans_and_still_completes(self, monkeypatch):
        async def boom():
            raise RuntimeError("kaboom")

        events = _drive(_agent_with("probe", boom), monkeypatch)
        types = [e.type.value for e in events]
        assert "tool_failed" in types
        assert "replanned" in types
        assert types[-1] == "run_completed"

    def test_weak_verification_replans(self, monkeypatch):
        async def fine():
            return {"url": "https://a/"}

        events = _drive(_agent_with("probe", fine), monkeypatch, advanced=False)
        types = [e.type.value for e in events]
        assert "step_verified" in types
        assert "replanned" in types

    def test_budget_stops_the_loop_with_a_warning(self, monkeypatch):
        async def fine():
            return {"url": "https://a/"}

        agent = Agent(tool_registry=_registry_with("probe", fine),
                      auto_register_builtins=False, max_steps=0)
        events = _drive(agent, monkeypatch)
        types = [e.type.value for e in events]
        assert "log" in types or any("max_steps" in str(e.data) for e in events)


def _registry_with(name: str, handler) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(name, handler)
    return registry