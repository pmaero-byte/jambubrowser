"""
Policies: the thing under test
==============================

A policy turns a task into an answer using a search tool. The published harness
swaps the *search backend* while holding the scout policy fixed, and that is the
comparison this module makes too.

Two policies are provided, and the gap between them is the measurement:

``naive``
    One search with the query as written; results used in engine order. This is
    what a search tool returns with no retrieval work on top.

``retrieval``
    Query expansion across several phrasings, cross-engine dedup, then ranking
    by term overlap with title matches weighted higher. Same corpus, same
    grader, same rounds — only the retrieval differs.

Both harvest from the documents they retrieve, so the score reflects what the
search backend surfaced, not any model in the loop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from backend.bench.runner import SearchFn, Task
from backend.modules.retrieval import dedupe, expand_query, rank, terms


def _harvest(rows: Sequence[dict], task: Task) -> Any:
    """Turn retrieved documents into an answer of the shape the task expects."""
    if task.metric == "entities":
        found: List[dict] = []
        seen = set()
        for row in rows:
            for entity in row.get("entities", []) or []:
                name = str(entity)
                if name.lower() not in seen:
                    seen.add(name.lower())
                    found.append({"name": name})
        return found
    if task.metric == "f1_items":
        items: List[str] = []
        seen = set()
        for row in rows:
            for fact in row.get("facts", []) or []:
                if fact not in seen:
                    seen.add(fact)
                    items.append(fact)
        return "; ".join(items)
    if task.metric == "table_f1":
        return [
            {"name": str(row.get("entities", [""])[0]) if row.get("entities") else row.get("title", ""),
             "price": str(row.get("facts", [""])[0]) if row.get("facts") else ""}
            for row in rows
            if row.get("entities")
        ]
    # exact: the best-matching document's content
    return str(rows[0].get("content", "")) if rows else ""


def naive_policy(task: Task, fn: SearchFn) -> Any:
    """One search, results in engine order, no dedup or ranking."""
    rows = fn.call(task.query)
    return _harvest(rows, task)


def retrieval_policy(task: Task, fn: SearchFn) -> Any:
    """Expand the query, merge, dedup and rank before harvesting."""
    variants = expand_query(task.query, max_variants=3)
    merged: List[dict] = []
    for variant in variants:
        merged.extend(fn.call(variant))
    ranked = rank(dedupe(merged), task.query, limit=fn.results_per_search)
    return _harvest(ranked, task)


def retrieval_no_expansion_policy(task: Task, fn: SearchFn) -> Any:
    """Dedup and rank, but no query expansion.

    Isolates how much of the gain comes from expansion rather than from
    cleaning up the result set.
    """
    rows = fn.call(task.query)
    ranked = rank(dedupe(rows), task.query, limit=fn.results_per_search)
    return _harvest(ranked, task)


POLICIES = {
    "naive": naive_policy,
    "retrieval": retrieval_policy,
    "retrieval_no_expansion": retrieval_no_expansion_policy,
}
