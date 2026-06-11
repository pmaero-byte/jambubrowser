"""
Jambubrowser Evaluation Harness
================================

A lightweight benchmark framework for measuring research-agent quality across
providers. Inspired by GAIA (multi-step reasoning) and WebArena (browser-based
task completion) but uses small, self-contained task suites so the harness can
run in minutes, not days.

What's measured
---------------
- **Success rate** — % of tasks where the agent's final answer matches the
  expected answer (exact or fuzzy match).
- **Latency** — wall-clock time per task.
- **Cost** — token usage × per-provider pricing.
- **Step count** — for the full agent, how many tool calls it took.
- **Memory** — did the agent use the memory layer effectively?

How to run
----------
    python -m backend.eval run --suite smoke --provider mock
    python -m backend.eval run --suite gaia-mini --provider anthropic
    python -m backend.eval compare --suite smoke --providers mock,ollama,anthropic
    python -m backend.eval report --run-id <id> --format markdown

Public API
----------
- `Harness`              — main runner
- `Task`                 — task definition
- `TaskResult`           — single task outcome
- `SuiteResult`          — collection of task results
- `Metric`               — registered metric
- `register_task`        — task decorator
- `run_suite`            — convenience: run a named suite
- `compare_providers`    — run the same suite across multiple providers
"""

from .harness import (
    Harness,
    Task,
    TaskResult,
    SuiteResult,
    TaskStatus,
    register_task,
    get_task,
    list_tasks,
    list_suites,
)
from .metrics import Metric, exact_match, contains_match, fuzzy_match, all_metrics
from .store import ResultsStore, get_store
from .report import generate_report
from .cli import main as cli_main

__all__ = [
    "Harness",
    "Task",
    "TaskResult",
    "SuiteResult",
    "TaskStatus",
    "register_task",
    "get_task",
    "list_tasks",
    "list_suites",
    "Metric",
    "exact_match",
    "contains_match",
    "fuzzy_match",
    "all_metrics",
    "ResultsStore",
    "get_store",
    "generate_report",
    "cli_main",
]
