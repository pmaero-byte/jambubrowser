"""Mission result diff utility.

Compares two mission result rows (or their text + sources payloads) and
produces a structured diff suitable for a UI panel or CLI output. Three
comparison axes are computed:

1. Sources diff: which URLs appeared, disappeared, or stayed.
2. Text diff: length delta, word set diff, and a simple Jaccard-like
   similarity score (no external deps).
3. Status diff: did success/failure change.

The diff is deliberately side-effect free and dependency-free so it can
be tested in isolation and used by either the API endpoint or the CLI.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def tokenize(text: str) -> set:
    """Return the lowercased set of words in *text*.

    Empty / None input returns an empty set (not None) so callers can
    freely union/intersect without NoneType errors.
    """
    if not text:
        return set()
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)}


def diff_sources(sources_a: Iterable[str], sources_b: Iterable[str]) -> dict:
    """Return a structured diff of two source-URL iterables.

    Returns a dict with three keys:
      - added:    URLs in B but not in A (new citations)
      - removed:  URLs in A but not in B (lost citations)
      - kept:     URLs in both
    """
    a = {s for s in (sources_a or []) if s}
    b = {s for s in (sources_b or []) if s}
    return {
        "added": sorted(b - a),
        "removed": sorted(a - b),
        "kept": sorted(a & b),
    }


def diff_text(text_a: Optional[str], text_b: Optional[str]) -> dict:
    """Return a structured diff of two result-text strings.

    Returns:
      - length_a, length_b: raw character counts
      - length_delta: b - a (positive = B is longer)
      - words_a, words_b: word counts
      - words_added, words_removed: words unique to B / unique to A
      - similarity: Jaccard index over word sets (0..1, rounded to 3dp)
      - changed: True if word sets differ
    """
    a = text_a or ""
    b = text_b or ""
    words_a = tokenize(a)
    words_b = tokenize(b)
    union = words_a | words_b
    intersection = words_a & words_b
    similarity = round(len(intersection) / len(union), 3) if union else 1.0
    return {
        "length_a": len(a),
        "length_b": len(b),
        "length_delta": len(b) - len(a),
        "words_a": len(words_a),
        "words_b": len(words_b),
        "words_added": sorted(words_b - words_a)[:20],
        "words_removed": sorted(words_a - words_b)[:20],
        "similarity": similarity,
        "changed": words_a != words_b,
    }


def diff_status(success_a: Optional[bool], success_b: Optional[bool]) -> dict:
    """Return a tiny diff of two success/failure flags."""
    return {
        "success_a": bool(success_a) if success_a is not None else None,
        "success_b": bool(success_b) if success_b is not None else None,
        "changed": bool(success_a) != bool(success_b),
    }


def compare_results(
    result_a: dict,
    result_b: dict,
    *,
    sources_a: Optional[Iterable[str]] = None,
    sources_b: Optional[Iterable[str]] = None,
) -> dict:
    """Compare two result dicts and return a unified diff payload.

    The input dicts should have at least: ``result_text`` (str) and
    ``success`` (bool). The optional ``sources_*`` arguments accept URL
    iterables that aren't stored in the result dict itself but come from
    the caller (e.g. parsed from sources_json).
    """
    text_diff = diff_text(result_a.get("result_text"), result_b.get("result_text"))
    status_diff = diff_status(result_a.get("success"), result_b.get("success"))
    src_diff = diff_sources(sources_a, sources_b)
    return {
        "result_a": {
            "id": result_a.get("id"),
            "run_at": result_a.get("run_at"),
        },
        "result_b": {
            "id": result_b.get("id"),
            "run_at": result_b.get("run_at"),
        },
        "text": text_diff,
        "sources": src_diff,
        "status": status_diff,
    }
