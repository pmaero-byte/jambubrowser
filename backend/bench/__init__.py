"""
Benchmark harness for agentic search.

Protocol-faithful to the harness behind the published agentic-search tables
(``exa-labs/benchmarks``, MIT): 25 rounds, 10 results per search, a terminal
submit, degraded marking, context truncation, and bootstrap CIs.

The graders here are deterministic rather than judge-based, so a run is free
and reproducible — which is what makes it useful for measuring a retrieval
change rather than for a leaderboard entry.

See ``docs/SEARCH_BENCHMARK_2026.md`` for the competitive analysis.
"""

from .grader import Grade, grade, grade_entities, grade_exact, grade_f1_items, grade_table
from .runner import (
    BenchReport,
    RunResult,
    SearchFn,
    Task,
    format_report,
    load_local_suite,
    load_tasks,
    run_suite,
)

__all__ = [
    "Grade", "grade", "grade_exact", "grade_f1_items", "grade_entities", "grade_table",
    "BenchReport", "RunResult", "SearchFn", "Task",
    "run_suite", "load_tasks", "load_local_suite", "format_report",
]
