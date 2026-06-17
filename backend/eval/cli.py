"""
CLI entrypoint for the eval harness.

Usage:
    python -m backend.eval run --suite smoke --provider mock
    python -m backend.eval compare --suite smoke --providers mock,ollama,anthropic
    python -m backend.eval report --run-id <id> --format markdown
    python -m backend.eval list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

# Importing the tasks package triggers all @register_task decorators.
from . import tasks as _tasks  # noqa: F401


def _cmd_run(args) -> int:
    from .harness import Harness
    from .harness import list_suites, list_tasks
    from .store import get_store
    from .report import generate_report

    # Validate suite name up-front so the user gets a clean error rather than
    # a Python traceback if they typo the suite.
    if args.suite not in list_suites():
        available = ", ".join(sorted(list_suites())) or "(none registered)"
        print(
            f"Error: unknown suite {args.suite!r}. Available suites: {available}",
            file=sys.stderr,
        )
        return 2

    harness = Harness(provider=args.provider, model=args.model)
    sr = asyncio.run(harness.run_suite(args.suite, task_ids=args.tasks))
    get_store().save_suite(sr)
    print(generate_report(sr, fmt=args.format))
    if args.out:
        with open(args.out, "w") as f:
            f.write(generate_report(sr, fmt=args.format))
        print(f"\n→ Saved to {args.out}", file=sys.stderr)
    return 0 if sr.success_rate >= args.min_pass_rate else 1


def _cmd_compare(args) -> int:
    from .harness import Harness
    from .store import get_store
    from .report import generate_report

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    out: list = []
    for p in providers:
        h = Harness(provider=p, model=args.model)
        sr = asyncio.run(h.run_suite(args.suite, task_ids=args.tasks))
        get_store().save_suite(sr)
        out.append(sr)
        print(f"\n{'='*60}\n{p}/{args.model} on {args.suite}: "
              f"{sr.passed}/{sr.total} ({sr.success_rate*100:.1f}%) "
              f"${sr.total_cost_usd:.4f} {sr.avg_duration_ms:.0f}ms", file=sys.stderr)
    print(generate_report(out, fmt=args.format))
    if args.out:
        with open(args.out, "w") as f:
            f.write(generate_report(out, fmt=args.format))
    return 0


def _cmd_report(args) -> int:
    from .store import get_store
    from .report import generate_report
    from .harness import SuiteResult

    store = get_store()
    if args.run_id:
        run = store.get_run(args.run_id)
        if not run:
            print(f"Run {args.run_id} not found", file=sys.stderr)
            return 1
        # Reconstruct SuiteResult
        sr = SuiteResult(
            run_id=run["run_id"],
            suite=run["suite"],
            provider=run["provider"],
            model=run["model"],
            started_at=run["started_at"],
            completed_at=run["completed_at"],
        )
        # Reconstruct TaskResults
        from .harness import TaskResult, TaskStatus
        for t in run.get("tasks", []):
            sr.results.append(TaskResult(
                task_id=t["task_id"],
                suite=t.get("suite") or run["suite"],
                provider=run["provider"],
                model=run["model"],
                status=TaskStatus(t.get("status") or "failed"),
                score=t.get("score", 0.0),
                duration_ms=t.get("duration_ms", 0.0),
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=t.get("total_tokens", 0),
                cost_usd=t.get("cost_usd", 0.0),
                steps=t.get("steps", 0),
                answer=t.get("answer", ""),
                expected=t.get("expected", ""),
                error=t.get("error"),
                run_id=run["run_id"],
            ))
        print(generate_report(sr, fmt=args.format))
    else:
        runs = store.list_runs(suite=args.suite, limit=args.limit)
        if not runs:
            print("No runs found", file=sys.stderr)
            return 1
        for r in runs:
            print(f"  {r['run_id']}  {r['suite']:20s}  {r['provider']:12s}  "
                  f"{r['success_rate']*100:5.1f}%  ({r['passed']}/{r['total']})  "
                  f"${r['total_cost_usd']:.4f}  {r['started_at']:.0f}")
    return 0


def _cmd_list(args) -> int:
    from .harness import list_tasks, list_suites
    suites = list_suites()
    print(f"Suites ({len(suites)}):")
    for s in suites:
        tasks = list_tasks(suite=s)
        print(f"  {s:20s}  ({len(tasks)} tasks)")
        for t in tasks:
            print(f"    {t.id:30s}  cat={t.category:10s}  diff={t.difficulty}  agent={t.use_agent}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.eval", description="Jambubrowser benchmark harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # run
    p_run = sub.add_parser("run", help="Run a single suite against a provider")
    p_run.add_argument("--suite", required=True, help="Suite name (e.g. smoke, gaia-mini)")
    p_run.add_argument("--provider", default=None, help="Provider name (default: env-driven)")
    p_run.add_argument("--model", default=None, help="Model override")
    p_run.add_argument("--tasks", nargs="*", default=None, help="Specific task IDs to run")
    p_run.add_argument("--format", default="markdown", choices=["markdown", "json"])
    p_run.add_argument("--out", default=None, help="Write report to this file")
    p_run.add_argument("--min-pass-rate", type=float, default=0.0, help="Exit non-zero if below this rate")
    p_run.set_defaults(func=_cmd_run)

    # compare
    p_cmp = sub.add_parser("compare", help="Run a suite across multiple providers")
    p_cmp.add_argument("--suite", required=True)
    p_cmp.add_argument("--providers", required=True, help="Comma-separated provider list")
    p_cmp.add_argument("--model", default=None)
    p_cmp.add_argument("--tasks", nargs="*", default=None)
    p_cmp.add_argument("--format", default="markdown", choices=["markdown", "json"])
    p_cmp.add_argument("--out", default=None)
    p_cmp.set_defaults(func=_cmd_compare)

    # report
    p_rep = sub.add_parser("report", help="View past runs")
    p_rep.add_argument("--run-id", default=None, help="Show full report for a run")
    p_rep.add_argument("--suite", default=None, help="Filter by suite")
    p_rep.add_argument("--limit", type=int, default=20)
    p_rep.add_argument("--format", default="markdown", choices=["markdown", "json"])
    p_rep.set_defaults(func=_cmd_report)

    # list
    sub.add_parser("list", help="List all available tasks and suites").set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
