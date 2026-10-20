"""
Deterministic graders for agentic-search evaluation
====================================================

The published agentic-search benchmarks all grade with a frontier LLM judge.
That is fine for a leaderboard and useless for local iteration: it costs money
per run, it makes results model-dependent, and — as the DSQA dataset card
warns explicitly — "using a different autorater model or grading prompt will
likely result in statistically significant deviation in results".

These graders need **no judge**. They are exact, deterministic, and cover the
shapes the published benchmarks measure:

* ``exact``     — normalised exact match (BrowseComp-style single answer)
* ``f1_items``  — F1 over answer items (DSQA-style set answers)
* ``entities``  — mean passed entities per task (FindAll-style)
* ``table_f1``  — cell-level F1 with row-level precision/recall (WideSearch-style)

A deterministic grader cannot replace a judge — it is stricter and it cannot
credit a paraphrase. It is the right tool for *relative* comparisons: if a
change to retrieval moves the number, the change is real.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalise(text: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT_RE.sub(" ", text.lower())
    return _WS_RE.sub(" ", text).strip()


def _split_items(text: str) -> List[str]:
    """Split a set answer into its items."""
    if not text:
        return []
    parts = re.split(r"[;\n]|(?<=[a-z0-9])\s*,\s*(?=[A-Z0-9])", text)
    return [p.strip() for p in parts if p.strip()]


@dataclass
class Grade:
    """One graded task.

    ``score`` is the metric-native value (0-1 for accuracy/F1, a *count* for
    entity recall). ``normalised`` puts every metric on a 0-1 scale so a single
    headline number can average across metrics without an entity task counting
    for three.
    """

    score: float = 0.0
    metric: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    normalised: Optional[float] = None

    @property
    def passed(self) -> bool:
        return self.score > 0

    def normalised_score(self) -> float:
        if self.normalised is not None:
            return self.normalised
        return max(0.0, min(1.0, self.score))


def grade_exact(prediction: str, expected: str, aliases: Sequence[str] = ()) -> Grade:
    """Normalised exact match against the answer or any alias."""
    target = normalise(expected)
    options = [target] + [normalise(a) for a in aliases]
    got = normalise(prediction)
    ok = bool(got) and any(got == o for o in options if o)
    # An "answer" that contains the expected string is usually right too.
    if not ok and got:
        ok = any(o and o in got for o in options if o)
    return Grade(score=1.0 if ok else 0.0, metric="exact", detail={"prediction": got})


def grade_f1_items(prediction: str, expected: str) -> Grade:
    """F1 over answer items (DSQA-style).

    Precision penalises extra items, recall penalises missing ones, and the
    harmonic mean punishes both — which is what the DSQA rubric does.
    """
    gold = [normalise(g) for g in _split_items(expected) if normalise(g)]
    got = [normalise(p) for p in _split_items(prediction) if normalise(p)]
    if not gold:
        return Grade(score=0.0, metric="f1_items", detail={"reason": "no expected items"})

    found = 0
    for g in gold:
        # Each gold item is judged independently — the same way the DSQA
        # rubric asks "is this gold item present in the response" for every
        # item. Consuming a response item per match would under-count an
        # answer that packs several facts into one line.
        if any(p and (p == g or g in p or p in g) for p in got):
            found += 1
    excessive = sum(
        1 for p in got
        if not any(g and (p == g or g in p or p in g) for g in gold)
    )
    precision = found / (found + excessive) if (found + excessive) else 0.0
    recall = found / len(gold)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Grade(
        score=f1, metric="f1_items",
        detail={"found": found, "expected": len(gold), "excessive": excessive,
                "precision": round(precision, 4), "recall": round(recall, 4)},
    )


def grade_entities(found: Iterable[dict], expected: Sequence[str],
                   key: str = "name") -> Grade:
    """Mean passed entities per task (FindAll-style).

    An entity passes when its name matches a gold name, exactly or after
    normalisation. Aliases are accepted so a legitimately different spelling
    still counts.
    """
    gold = [normalise(g) for g in expected if normalise(g)]
    gold_set: Set[str] = set(gold)
    passed = 0
    names: List[str] = []
    for entity in found or []:
        if not isinstance(entity, dict):
            continue
        name = normalise(str(entity.get(key, "")))
        names.append(name)
        if name and (name in gold_set or any(name in g or g in name for g in gold_set if g)):
            passed += 1
    total = len(names)
    expected_count = len(gold)
    return Grade(
        score=float(passed), metric="entities",
        normalised=(passed / expected_count) if expected_count else 0.0,
        detail={"passed": passed, "returned": total, "expected": expected_count,
                "precision": (passed / total) if total else 0.0},
    )


def _cell_equal(a: str, b: str) -> bool:
    na, nb = normalise(a), normalise(b)
    if na == nb:
        return True
    # Numbers within 10% — the tolerance WideSearch's own rubric uses.
    try:
        fa, fb = float(str(a).replace(",", "").replace("%", "")), float(str(b).replace(",", "").replace("%", ""))
        if fa == fb:
            return True
        scale = max(abs(fa), abs(fb), 1e-9)
        return abs(fa - fb) / scale <= 0.1
    except (TypeError, ValueError):
        return False


def grade_table(predicted: Sequence[dict], gold: Sequence[dict],
                key: str = "name") -> Grade:
    """Row-level precision/recall/F1 over table rows (WideSearch-style).

    A row is correct when its key matches a gold row and every graded cell
    matches. Precision is over rows returned, recall over rows expected.
    """
    gold_rows = list(gold or [])
    pred_rows = list(predicted or [])
    if not gold_rows:
        return Grade(score=0.0, metric="table_f1", detail={"reason": "no gold rows"})

    remaining = list(gold_rows)
    true_rows = 0
    for row in pred_rows:
        name = normalise(str(row.get(key, "")))
        for i, g in enumerate(remaining):
            gname = normalise(str(g.get(key, "")))
            if not name or not gname or name != gname:
                continue
            cells_ok = all(
                _cell_equal(str(row.get(k, "")), str(v))
                for k, v in g.items()
                if k != key and k in row
            )
            if cells_ok:
                remaining.pop(i)
                true_rows += 1
            break

    precision = true_rows / len(pred_rows) if pred_rows else 0.0
    recall = true_rows / len(gold_rows)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Grade(
        score=f1, metric="table_f1",
        detail={"true_rows": true_rows, "predicted_rows": len(pred_rows),
                "gold_rows": len(gold_rows), "precision": round(precision, 4),
                "recall": round(recall, 4)},
    )


def grade_answer_in_text(prediction: str, expected: str) -> Grade:
    """Does the retrieved text actually contain the answer?

    A deterministic stand-in for the BrowseComp-Plus judge: the official
    metric is an LLM judge, which is neither free nor reproducible, so this
    asks the stricter question — is the gold answer present in the context the
    agent was given? An agent cannot answer from a context that does not
    contain the answer, so this is a sound (if strict) upper bound.
    """
    got = normalise(prediction)
    want = normalise(expected)
    if not want:
        return Grade(score=0.0, metric="answer_in_text", detail={"reason": "no expected answer"})
    ok = want in got
    return Grade(score=1.0 if ok else 0.0, metric="answer_in_text",
                 detail={"expected": want[:80], "found": ok})


GRADERS = {
    "exact": grade_exact,
    "f1_items": grade_f1_items,
    "entities": grade_entities,
    "table_f1": grade_table,
    "answer_in_text": grade_answer_in_text,
}


def grade(task: Any, prediction: Any) -> Grade:
    """Grade one task using the metric named in the task.

    Accepts either a plain dict or a :class:`backend.bench.runner.Task`.
    """
    if isinstance(task, dict):
        metric = task.get("metric", "exact")
        expected = task.get("expected", "")
        aliases = task.get("aliases", [])
    else:
        metric = getattr(task, "metric", "exact")
        expected = getattr(task, "expected", "")
        aliases = getattr(task, "aliases", [])

    grader = GRADERS.get(metric)
    if grader is None:
        raise ValueError(f"unknown metric {metric!r}")
    if metric == "exact":
        return grader(str(prediction or ""), expected, aliases)
    if metric == "f1_items":
        return grader(str(prediction or ""), expected)
    if metric == "entities":
        return grader(prediction or [], expected)
    if metric == "table_f1":
        return grader(prediction or [], expected)
    if metric == "answer_in_text":
        return grader(str(prediction or ""), expected)
    raise ValueError(f"unhandled metric {metric!r}")
