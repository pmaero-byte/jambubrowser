"""
Built-in metrics for grading task results.

Each metric is a function that takes (answer, expected) and returns a float
score in [0.0, 1.0].
"""

from __future__ import annotations

import re
from typing import Callable, Union


Metric = Callable[[str, Union[str, list[str]]], float]


def exact_match(answer: str, expected: Union[str, list[str]]) -> float:
    """1.0 if the answer matches any expected value exactly (case-insensitive, stripped)."""
    if not answer:
        return 0.0
    a = answer.strip().lower()
    candidates = expected if isinstance(expected, list) else [expected]
    return 1.0 if any(a == str(c).strip().lower() for c in candidates) else 0.0


def contains_match(answer: str, expected: Union[str, list[str]]) -> float:
    """1.0 if any expected value appears in the answer (case-insensitive)."""
    if not answer:
        return 0.0
    a = answer.lower()
    candidates = expected if isinstance(expected, list) else [expected]
    return 1.0 if any(str(c).lower() in a for c in candidates) else 0.0


def fuzzy_match(answer: str, expected: Union[str, list[str]]) -> float:
    """Partial credit: fraction of significant words from expected found in answer."""
    if not answer:
        return 0.0
    a = set(re.findall(r"\b\w{4,}\b", answer.lower()))
    candidates = expected if isinstance(expected, list) else [expected]
    if not candidates:
        return 0.0
    scores: list[float] = []
    for c in candidates:
        words = set(re.findall(r"\b\w{4,}\b", str(c).lower()))
        if not words:
            continue
        scores.append(len(a & words) / len(words))
    return max(scores) if scores else 0.0


def number_match(answer: str, expected: Union[str, list[str]]) -> float:
    """Extract a number from answer and compare. Useful for arithmetic tasks."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", answer.replace(",", ""))
    if not nums:
        return 0.0
    candidates = expected if isinstance(expected, list) else [expected]
    for c in candidates:
        try:
            target = float(str(c).replace(",", ""))
        except (TypeError, ValueError):
            continue
        for n in nums:
            try:
                if abs(float(n) - target) < 1e-6:
                    return 1.0
            except ValueError:
                continue
    return 0.0


def email_redaction_match(answer: str, expected: Union[str, list[str]]) -> float:
    """For privacy/PI-redaction tasks: 1.0 if no email pattern remains in answer."""
    if not answer:
        return 0.0
    email_pat = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    leaked = re.findall(email_pat, answer)
    if not leaked:
        return 1.0
    return max(0.0, 1.0 - len(leaked) * 0.3)


# Registry of all built-in metrics
ALL_METRICS: dict[str, Metric] = {
    "exact_match": exact_match,
    "contains_match": contains_match,
    "fuzzy_match": fuzzy_match,
    "number_match": number_match,
    "email_redaction_match": email_redaction_match,
}


def all_metrics() -> dict[str, Metric]:
    """Return a copy of the registered metrics."""
    return dict(ALL_METRICS)
