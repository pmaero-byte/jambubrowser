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


def _build_index(args, retriever: str = None):
    """Build the retrieval index for the requested retriever."""
    from backend.bench.corpus import LocalIndex
    from backend.modules.semantic import build_index

    retriever = retriever or args.retriever
    index = _load_corpus(args.suite)
    if retriever == "lexical":
        return index, None
    rows = [d.to_dict() for d in index.documents]
    built = build_index(rows, retriever=retriever, embedder_kind=args.embedder)
    reason = getattr(getattr(built, "semantic", built), "unavailable_reason", "")
    if hasattr(built, "dense_weight"):
        built.dense_weight = args.dense_weight
    return built, reason


def cmd_bench(args):
    from backend.bench.runner import format_report, load_local_suite, run_suite

    try:
        tasks = load_local_suite(args.suite)
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return core.EXIT_ERROR

    try:
        index, unavailable = _build_index(args)
    except Exception as e:  # noqa: BLE001
        print(f"✗ could not build the {args.retriever} retriever: {type(e).__name__}: {e}")
        return core.EXIT_ERROR
    if unavailable:
        print(f"! dense retriever unavailable ({unavailable}); falling back to lexical")

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

    if args.retriever_compare:
        print("\n— retrievers (same policy, same grader) —")
        for name in ("lexical", "dense", "hybrid"):
            try:
                other_index, reason = _build_index(args, retriever=name)
            except Exception as e:  # noqa: BLE001
                print(f"  {name:28} unavailable ({type(e).__name__}: {e})")
                continue
            if reason:
                print(f"  {name:28} unavailable ({reason})")
                continue
            other = run_suite(
                tasks,
                policy,
                search=lambda q, l, _i=other_index: _i.search(q, limit=l),
                name=name,
                max_rounds=args.max_rounds,
                results_per_search=args.results_per_search,
            )
            lo, hi = other.bootstrap_ci()
            print(f"  {name:28} {other.mean:.4f}  CI95 [{lo:.4f}, {hi:.4f}]")

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
    p.add_argument(
        "--retriever", default="lexical", choices=("lexical", "dense", "hybrid"),
        help="Search backend under test (default: lexical)",
    )
    p.add_argument(
        "--embedder", default="lsa", choices=("lsa", "sentence-transformers"),
        help="Dense backend: 'lsa' needs only numpy; 'sentence-transformers' needs the model",
    )
    p.add_argument(
        "--dense-weight", type=float, default=0.5,
        help="How much the dense side counts in hybrid fusion (default 0.5; "
             "equal weight measured worse than lexical alone)",
    )
    p.add_argument("--max-rounds", type=int, default=25,
                   help="Search rounds per task (default: 25, matching the reference harness)")
    p.add_argument("--results-per-search", type=int, default=10,
                   help="Results per search (default: 10, matching the reference harness)")
    p.add_argument("--compare", action="store_true",
                   help="Also run the other policies and show the comparison")
    p.add_argument("--retriever-compare", action="store_true",
                   help="Also run lexical / dense / hybrid and show the comparison")
    p.add_argument("--json", action="store_true", help="Emit machine-readable results")
