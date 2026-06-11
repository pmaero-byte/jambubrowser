"""
Core benchmark harness.

Defines Task, runs a suite of tasks against a provider (optionally via the
agent loop), collects TaskResults, and computes aggregate metrics.

Design goals:
- Small, self-contained tasks (no external network when possible)
- Pluggable execution modes: simple (direct LLM) vs agent (full ReAct loop)
- Per-task + per-suite metrics with cost attribution
- SQLite-backed result storage for longitudinal comparison
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union

from backend.llm import ChatMessage, Role, get_registry, get_default

log = logging.getLogger("jambu.eval.harness")


class TaskStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"      # harness error, not a task failure
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class Task:
    """A single benchmark task."""
    id: str
    suite: str
    prompt: str
    expected: Union[str, list[str]]
    category: str = "general"            # "qa", "research", "browser", "memory", "privacy"
    difficulty: int = 1                  # 1-5
    timeout_seconds: float = 30.0
    max_steps: int = 5                   # for agent mode
    system: Optional[str] = None         # custom system prompt
    metadata: dict = field(default_factory=dict)
    # Optional pre/post hooks for custom assertions
    grader: Optional[Callable[[str, "TaskResult"], bool]] = None
    # Use the full agent loop (vs single-shot LLM)
    use_agent: bool = False


@dataclass
class TaskResult:
    """Outcome of running a single task."""
    task_id: str
    suite: str
    provider: str
    model: str
    status: TaskStatus
    answer: str = ""
    expected: str = ""
    score: float = 0.0                   # 0.0-1.0
    duration_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    steps: int = 0                       # tool calls (for agent mode)
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""                     # shared across the suite

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "suite": self.suite,
            "provider": self.provider,
            "model": self.model,
            "status": self.status.value,
            "score": self.score,
            "duration_ms": self.duration_ms,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "steps": self.steps,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class SuiteResult:
    """Aggregate result for a suite run."""
    run_id: str
    suite: str
    provider: str
    model: str
    results: list[TaskResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TaskStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TaskStatus.FAILED)

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.status == TaskStatus.ERROR)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.passed / self.total

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.results)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)

    @property
    def avg_duration_ms(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.duration_ms for r in self.results) / len(self.results)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "suite": self.suite,
            "provider": self.provider,
            "model": self.model,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "total": self.total,
            "success_rate": self.success_rate,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "avg_duration_ms": self.avg_duration_ms,
            "duration_seconds": (self.completed_at or time.time()) - self.started_at,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Task registry
# ---------------------------------------------------------------------------

_TASKS: dict[str, Task] = {}


def register_task(task: Task) -> Task:
    """Register a task. Idempotent: re-registering overwrites."""
    _TASKS[task.id] = task
    return task


def get_task(task_id: str) -> Task:
    if task_id not in _TASKS:
        raise KeyError(f"Task {task_id!r} not registered. Known: {sorted(_TASKS)}")
    return _TASKS[task_id]


def list_tasks(suite: Optional[str] = None) -> list[Task]:
    items = list(_TASKS.values())
    if suite:
        items = [t for t in items if t.suite == suite]
    return sorted(items, key=lambda t: (t.suite, t.id))


def list_suites() -> list[str]:
    return sorted({t.suite for t in _TASKS.values()})


# ---------------------------------------------------------------------------
# Default grader
# ---------------------------------------------------------------------------

def _default_grade(answer: str, expected: Union[str, list[str]]) -> tuple[bool, float]:
    """Default grading strategy: any expected value appears in the answer (case-insensitive)."""
    if not answer:
        return False, 0.0
    answer_norm = answer.lower().strip()
    candidates = expected if isinstance(expected, list) else [expected]
    candidates = [str(c).lower().strip() for c in candidates if c]
    if not candidates:
        return False, 0.0
    if any(c in answer_norm for c in candidates):
        return True, 1.0
    # Partial credit: any word from any expected in the answer
    matches = sum(1 for c in candidates if any(w in answer_norm for w in c.split() if len(w) > 3))
    if matches:
        return False, matches / len(candidates)
    return False, 0.0


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class Harness:
    """Runs benchmark suites and collects results."""

    def __init__(self, *, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider  # None = use default
        self.model = model
        self._registry = get_registry()

    async def _execute_simple(self, task: Task) -> TaskResult:
        """Run task with a single LLM call (no agent)."""
        from backend.llm import ChatMessage, Role
        messages = []
        if task.system:
            messages.append(ChatMessage(role=Role.SYSTEM, content=task.system))
        messages.append(ChatMessage(role=Role.USER, content=task.prompt))
        resp = await self._registry.chat(
            messages,
            provider=self.provider,
            model=self.model,
            temperature=0.0,  # deterministic for benchmarks
            max_tokens=500,
        )
        return resp

    async def _execute_agent(self, task: Task) -> tuple[str, int, dict]:
        """Run task with the full ReAct agent loop."""
        from backend.agent import Agent
        from backend.llm import ChatMessage, Role
        agent = Agent(
            max_steps=task.max_steps,
            max_tokens=8000,
            max_seconds=task.timeout_seconds,
        )
        steps = 0
        last_usage = {}
        final_answer = ""
        # We bypass the async-iterator API for benchmarking and call the loop directly
        async for ev in agent.run(task.prompt, user_id="eval", context=""):
            if ev.type.value == "tool_called":
                steps += 1
            if ev.type.value == "answer_ready":
                final_answer = ev.data.get("answer", "")
                last_usage = ev.data.get("usage", {}) or {}
        return final_answer, steps, last_usage

    async def run_task(self, task: Task, *, run_id: Optional[str] = None) -> TaskResult:
        """Run a single task, return TaskResult."""
        run_id = run_id or uuid.uuid4().hex[:12]
        result = TaskResult(
            task_id=task.id,
            suite=task.suite,
            provider="",
            model="",
            status=TaskStatus.PASSED,  # optimistic; corrected below
            expected=task.expected if isinstance(task.expected, str) else (task.expected[0] if task.expected else ""),
            run_id=run_id,
        )
        started = time.monotonic()
        try:
            if task.use_agent:
                answer, steps, usage = await asyncio.wait_for(
                    self._execute_agent(task), timeout=task.timeout_seconds
                )
                result.steps = steps
                result.prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                result.completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                result.total_tokens = result.prompt_tokens + result.completion_tokens
            else:
                resp = await asyncio.wait_for(
                    self._execute_simple(task), timeout=task.timeout_seconds
                )
                answer = resp.content
                result.provider = resp.provider
                result.model = resp.model
                result.prompt_tokens = resp.usage.prompt_tokens
                result.completion_tokens = resp.usage.completion_tokens
                result.total_tokens = resp.usage.total_tokens
                result.cost_usd = resp.usage.cost_usd
            result.answer = answer
        except asyncio.TimeoutError:
            result.status = TaskStatus.TIMEOUT
            result.error = f"timeout after {task.timeout_seconds}s"
        except Exception as e:
            result.status = TaskStatus.ERROR
            result.error = str(e)[:500]
            log.warning("Task %s errored: %s", task.id, e)
        result.duration_ms = (time.monotonic() - started) * 1000

        # Grade
        if result.status in (TaskStatus.TIMEOUT, TaskStatus.ERROR):
            result.score = 0.0
        else:
            if task.grader:
                try:
                    passed = task.grader(result.answer, result)
                    result.score = 1.0 if passed else 0.0
                except Exception as e:
                    log.warning("Custom grader failed for %s: %s", task.id, e)
                    passed, result.score = _default_grade(result.answer, task.expected)
            else:
                passed, result.score = _default_grade(result.answer, task.expected)
            result.status = TaskStatus.PASSED if passed else TaskStatus.FAILED
        return result

    async def run_suite(
        self,
        suite: str,
        *,
        task_ids: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> SuiteResult:
        """Run all tasks in a suite (or a subset)."""
        tasks = list_tasks(suite=suite)
        if task_ids:
            tasks = [t for t in tasks if t.id in task_ids]
        if not tasks:
            raise ValueError(f"No tasks found for suite {suite!r}")
        run_id = uuid.uuid4().hex[:12]
        provider = self.provider or self._registry._config.default_provider
        model = self.model or self._registry._config.model_for(provider)
        sr = SuiteResult(
            run_id=run_id,
            suite=suite,
            provider=provider,
            model=model,
            metadata=metadata or {},
        )
        log.info("Running suite %s (%d tasks) on %s/%s [%s]",
                 suite, len(tasks), provider, model, run_id)
        for t in tasks:
            log.info("  → %s (cat=%s, diff=%d)", t.id, t.category, t.difficulty)
            r = await self.run_task(t, run_id=run_id)
            sr.results.append(r)
            log.info("    %s in %.0fms (score=%.2f)", r.status.value, r.duration_ms, r.score)
        sr.completed_at = time.time()
        return sr


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

async def run_suite(suite: str, *, provider: Optional[str] = None) -> SuiteResult:
    return await Harness(provider=provider).run_suite(suite)


async def compare_providers(
    suite: str,
    providers: list[str],
    *,
    task_ids: Optional[list[str]] = None,
) -> list[SuiteResult]:
    """Run the same suite against multiple providers sequentially."""
    out: list[SuiteResult] = []
    for p in providers:
        h = Harness(provider=p)
        sr = await h.run_suite(suite, task_ids=task_ids, metadata={"compare_run": True})
        out.append(sr)
    return out
