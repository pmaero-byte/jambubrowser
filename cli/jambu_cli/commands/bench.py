"""`jambu bench` — run the agentic-search benchmark locally.

Reports a mean score with a bootstrap confidence interval, so a retrieval change
can be judged rather than assumed. The grader is deterministic, so a run needs
no network, no API key and no judge model.
"""
from __future__ import annotations

import json

from cli.jambu_cli import core


def _load_corpus(suite: str):
    from backend.bench.corpus import LocalIndex
    from backend.bench.runner import local_suite_dir

    path = local_suite_dir() / "corpus.jsonl"
    return LocalIndex.load(path)


def _policy(name: str):
    from backend.bench.policies import POLICIES

    policy = POLICIES.get(name)
    if policy is None:
        raise SystemExit(f"unknown policy {name!r}; choose from {', '.join(sorted(POLICIES))}")
    return policy


def cmd_bench(args):
    from backend.bench.runner import format_report, load_local_suite, run_suite

    try:
        tasks = load_local_suite(args.suite)
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return core.EXIT_ERROR

    index = _load_corpus(args.suite)
    policy = _policy(args.policy)

    def search(query: str, limit: int = 10) -> list:
        return index.search(query, limit=limit)

    report = run_suite(
        tasks,
        policy,
        search=search,
        name=args.suite,
        max_rounds=args.max_rounds,
        results_per_search=args.results_per_search,
    )

    print(f"\n{format_report(report)}")

    if args.compare:
        print("\n— comparison —")
        rows = []
        for name in ("naive", "retrieval_no_expansion", "retrieval"):
            if name == args.policy:
                continue
            other = run_suite(
                tasks,
                _policy(name),
                search=search,
                name=name,
                max_rounds=args.max_rounds,
                results_per_search=args.results_per_search,
            )
            lo, hi = other.bootstrap_ci()
            rows.append((name, other.mean, lo, hi))
        for name, mean, lo, hi in sorted(rows, key=lambda r: -r[1]):
            print(f"  {name:28} {mean:.4f}  CI95 [{lo:.4f}, {hi:.4f}]")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    return core.EXIT_OK


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "bench",
        help="Run the local agentic-search benchmark (deterministic, offline)",
    )
    p.add_argument("--suite", default="local", help="Suite name (default: local)")
    p.add_argument(
        "--policy", default="retrieval",
        help="Retrieval policy: naive | retrieval_no_expansion | retrieval",
    )
    p.add_argument("--max-rounds", type=int, default=25,
                   help="Search rounds per task (default: 25, matching the reference harness)")
    p.add_argument("--results-per-search", type=int, default=10,
                   help="Results per search (default: 10, matching the reference harness)")
    p.add_argument("--compare", action="store_true",
                   help="Also run the other policies and show the comparison")
    p.add_argument("--json", action="store_true", help="Emit machine-readable results")
