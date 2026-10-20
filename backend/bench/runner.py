"""
Benchmark runner
================

Protocol-faithful to the harness behind the published agentic-search tables
(``exa-labs/benchmarks``, MIT), so numbers here are shaped like theirs:

* ``max_rounds`` (default 25) and ``results_per_search`` (default 10);
* a terminal submit — a run that never answers is a failure, not a near-miss;
* ``degraded`` marking when the budget is exhausted and the answer is
  synthesised from whatever evidence was gathered;
* context truncation when the accumulated evidence exceeds a budget, dropping
  the oldest exchange;
* 95% bootstrap confidence intervals over task resamples, seed 0.

What differs, deliberately: **the grader is deterministic, not a judge model.**
That makes a run reproducible and free, at the cost of being stricter than an
LLM judge — it cannot credit a paraphrase. So treat these numbers as a
*relative* measure: good for deciding whether a retrieval change helped, not as
a leaderboard entry.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.bench.grader import Grade, grade

DEFAULT_MAX_ROUNDS = 25
DEFAULT_RESULTS_PER_SEARCH = 10
DEFAULT_CONTEXT_CHARS = 120_000


@dataclass
class Task:
    id: str
    query: str
    metric: str = "exact"
    expected: Any = ""
    aliases: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "Task":
        return cls(
            id=str(row.get("id", "")),
            query=str(row.get("query", "")),
            metric=str(row.get("metric", "exact")),
            expected=row.get("expected", ""),
            aliases=list(row.get("aliases", [])),
            meta=dict(row.get("meta", {})),
        )


@dataclass
class RunResult:
    task_id: str
    score: float
    metric: str
    normalised: float = 0.0
    rounds_used: int = 0
    searches_used: int = 0
    degraded: bool = False
    context_truncations: int = 0
    duration_ms: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class BenchReport:
    name: str
    results: List[RunResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def mean(self) -> float:
        """Headline score: every task normalised to 0-1, then averaged.

        Raw means would let an entity task (a count) outweigh an accuracy task,
        so the raw per-metric numbers are reported separately in ``by_metric``.
        """
        if not self.results:
            return 0.0
        return sum(r.normalised for r in self.results) / len(self.results)

    @property
    def failures(self) -> int:
        return sum(1 for r in self.results if r.error)

    def bootstrap_ci(self, *, resamples: int = 10_000, seed: int = 0,
                     alpha: float = 0.05) -> tuple:
        """95% bootstrap CI over task resamples, seed 0 — the reference method."""
        scores = [r.normalised for r in self.results]
        if not scores:
            return (0.0, 0.0)
        rng = random.Random(seed)
        means: List[float] = []
        n = len(scores)
        for _ in range(resamples):
            means.append(sum(scores[rng.randrange(n)] for _ in range(n)) / n)
        means.sort()
        lo = means[int(alpha / 2 * resamples)]
        hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
        return (lo, hi)

    def by_metric(self) -> Dict[str, float]:
        """Raw, metric-native means (entity tasks report a count)."""
        groups: Dict[str, List[float]] = {}
        for r in self.results:
            groups.setdefault(r.metric, []).append(r.score)
        return {m: (sum(v) / len(v)) for m, v in groups.items()}

    def to_dict(self) -> Dict[str, Any]:
        lo, hi = self.bootstrap_ci()
        return {
            "suite": self.name,
            "tasks": self.n,
            "mean_score": round(self.mean, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "by_metric": {k: round(v, 4) for k, v in self.by_metric().items()},
            "errors": self.failures,
            "degraded": sum(1 for r in self.results if r.degraded),
            "mean_rounds": round(
                sum(r.rounds_used for r in self.results) / self.n, 2) if self.n else 0,
            "mean_searches": round(
                sum(r.searches_used for r in self.results) / self.n, 2) if self.n else 0,
            "context_truncations": sum(r.context_truncations for r in self.results),
            "results": [
                {"id": r.task_id, "score": round(r.score, 4),
                 "normalised": round(r.normalised, 4), "metric": r.metric,
                 "rounds": r.rounds_used, "searches": r.searches_used,
                 "degraded": r.degraded, "error": r.error, **r.detail}
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------
#
# A policy is the thing under test. In the published harness it is a scout
# agent with a search tool; here it is anything that turns a task into an
# answer. Splitting it out is what lets the *search backend* be swapped while
# the policy is held fixed — the comparison the published table makes.

Policy = Callable[[Task, "SearchFn"], Any]


@dataclass
class SearchFn:
    """The search tool handed to a policy: one call, one round.

    ``search`` is the *raw* search callable — ``(query, limit) -> rows`` — the
    thing under test. ``call`` wraps it with the protocol: round accounting,
    the per-search result cap, evidence accumulation and context truncation.
    """

    search: Callable[[str, int], List[dict]]
    rounds_used: int = 0
    searches_used: int = 0
    max_rounds: int = DEFAULT_MAX_ROUNDS
    results_per_search: int = DEFAULT_RESULTS_PER_SEARCH
    context_chars: int = DEFAULT_CONTEXT_CHARS
    context_used: int = 0
    context_truncations: int = 0
    degraded: bool = False
    evidence: List[dict] = field(default_factory=list)

    def call(self, query: str) -> List[dict]:
        """One search round. Returns the results, capped."""
        if self.rounds_used >= self.max_rounds:
            self.degraded = True
            return []
        self.rounds_used += 1
        self.searches_used += 1
        rows = self.search(query, self.results_per_search) or []
        for row in rows:
            row = dict(row)
            row["query"] = query
            self.evidence.append(row)
            self.context_used += len(str(row.get("title", ""))) + len(str(row.get("content", "")))
        # Context overflow: drop the oldest evidence, as the reference harness
        # does, rather than failing the run.
        while self.context_used > self.context_chars and len(self.evidence) > 1:
            dropped = self.evidence.pop(0)
            self.context_used -= len(str(dropped.get("title", ""))) + len(str(dropped.get("content", "")))
            self.context_truncations += 1
        return rows

    @property
    def exhausted(self) -> bool:
        return self.rounds_used >= self.max_rounds


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_suite(
    tasks: Sequence[Task],
    policy: Policy,
    *,
    search: Callable[[str, int], List[dict]],
    name: str = "suite",
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    results_per_search: int = DEFAULT_RESULTS_PER_SEARCH,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> BenchReport:
    """Run a policy over a task set and grade the results.

    ``search`` is the raw search backend under test — ``(query, limit) -> rows``.
    """
    report = BenchReport(name=name)
    for task in tasks:
        started = time.time()
        fn = SearchFn(
            search=search,
            max_rounds=max_rounds,
            results_per_search=results_per_search,
            context_chars=context_chars,
        )
        try:
            prediction = policy(task, fn)
            if fn.exhausted and not _submitted(prediction):
                fn.degraded = True
            g: Grade = grade(task, prediction)
            # Record what was actually retrieved, so a retrieval-only score can
            # be computed against relevance judgements without a judge model.
            g.detail = dict(g.detail or {})
            g.detail["retrieved"] = [
                str(r.get("id") or r.get("url") or "") for r in fn.evidence
            ]
            report.results.append(RunResult(
                task_id=task.id, score=g.score, metric=g.metric,
                normalised=g.normalised_score(),
                rounds_used=fn.rounds_used, searches_used=fn.searches_used,
                degraded=fn.degraded, context_truncations=fn.context_truncations,
                duration_ms=int((time.time() - started) * 1000),
                detail=g.detail,
            ))
        except Exception as e:  # noqa: BLE001 — one task must not stop the run
            report.results.append(RunResult(
                task_id=task.id, score=0.0, metric=task.metric, normalised=0.0,
                duration_ms=int((time.time() - started) * 1000),
                error=f"{type(e).__name__}: {e}",
            ))
    return report


def _submitted(prediction: Any) -> bool:
    """Whether the policy produced a terminal answer."""
    if prediction is None:
        return False
    if isinstance(prediction, str):
        return bool(prediction.strip())
    if isinstance(prediction, (list, dict)):
        return bool(prediction)
    return True


# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------

def load_tasks(path: str | Path) -> List[Task]:
    """Load a JSONL task file."""
    tasks: List[Task] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            tasks.append(Task.from_dict(json.loads(line)))
    return tasks


def local_suite_dir() -> Path:
    return Path(__file__).resolve().parent / "evals"


def load_local_suite(name: str = "local") -> List[Task]:
    """Load one of the built-in local suites."""
    path = local_suite_dir() / f"{name}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no local suite at {path}")
    return load_tasks(path)


def format_report(report: BenchReport) -> str:
    """A short human-readable summary."""
    lo, hi = report.bootstrap_ci()
    lines = [
        f"suite: {report.name}",
        f"tasks: {report.n}   mean: {report.mean:.4f}   95% CI: [{lo:.4f}, {hi:.4f}]",
    ]
    by_metric = report.by_metric()
    if by_metric:
        lines.append("by metric: " + ", ".join(f"{k}={v:.4f}" for k, v in sorted(by_metric.items())))
    lines.append(
        f"rounds/task: {sum(r.rounds_used for r in report.results) / max(1, report.n):.2f}"
        f"   searches/task: {sum(r.searches_used for r in report.results) / max(1, report.n):.2f}"
        f"   degraded: {sum(1 for r in report.results if r.degraded)}"
        f"   errors: {report.failures}"
    )
    worst = sorted(report.results, key=lambda r: r.score)[:5]
    if worst and report.n > 1:
        lines.append("weakest tasks: " + ", ".join(f"{r.task_id}({r.score:.2f})" for r in worst))
    return "\n".join(lines)
