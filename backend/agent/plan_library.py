"""Plan library: successful plans, cached as templates for future runs.

Every agent run historically started from zero: the planner re-derived a
plan for goals that are often near-identical. Procedural memory already
advises *how* to approach a query class; this store caches the actual
successful *plan* (ordered tool steps) keyed by a normalised goal, so a
repeat or near-repeat goal can start from the template that worked.

Matching policy, deliberately conservative:
* an exact normalised-key hit wins;
* otherwise a token-overlap (Jaccard) match of >= 0.5 wins, ranked by
  success-rate-adjusted score;
* a template is only *advised* to the planner — it is context, never a
  substitute for examining the live page/app. If advising fails, the run
  falls back to a cold start, exactly as before.

Persistence is a single JSON document; small by design (capped entries,
least-recently-used eviction).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("jambu.agent.plan_library")

_DEFAULT_PATH = os.path.expanduser("~/.jambubrowser/plan_library.json")
_MAX_ENTRIES = 200


def normalize_goal(query: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — stable template key."""
    q = (query or "").lower()
    q = re.sub(r"[^a-z0-9\s]+", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def _tokens(key: str) -> set[str]:
    return set(key.split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class PlanLibrary:
    """Thread-safe store of successful plan templates."""

    def __init__(self, path: Optional[str] = None):
        self._path = Path(path or os.environ.get("JAMBU_PLAN_LIBRARY_PATH") or _DEFAULT_PATH)
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            entries = data.get("entries") or {}
            if isinstance(entries, dict):
                self._entries = {k: v for k, v in entries.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._entries = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "saved_at": time.time(), "entries": self._entries}
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("plan library not persisted: %s", exc)

    # -- public API -----------------------------------------------------------

    def put(self, query: str, plan: dict[str, Any], *, success: bool = True) -> dict[str, Any]:
        """Record a plan template for a goal. `success=False` lowers its score."""
        with self._lock:
            self._load()
            key = normalize_goal(query)
            if not key:
                raise ValueError("empty goal")
            existing = self._entries.get(key)
            uses = int((existing or {}).get("uses", 0)) + 1
            successes = int((existing or {}).get("successes", 0)) + (1 if success else 0)
            entry = {
                "key": key,
                "query": query.strip(),
                "plan": plan,
                "uses": uses,
                "successes": successes,
                "success_rate": round(successes / max(uses, 1), 3),
                "updated_at": time.time(),
            }
            self._entries[key] = entry
            self._evict_if_needed()
            self._save()
            return entry

    def record_outcome(self, query: str, *, success: bool) -> Optional[dict[str, Any]]:
        key = normalize_goal(query)
        with self._lock:
            self._load()
            if key not in self._entries:
                return None
            self._entries[key]["uses"] = int(self._entries[key].get("uses", 0)) + 1
            if success:
                self._entries[key]["successes"] = int(self._entries[key].get("successes", 0)) + 1
            self._entries[key]["success_rate"] = round(
                int(self._entries[key]["successes"]) / max(int(self._entries[key]["uses"]), 1), 3
            )
            self._entries[key]["updated_at"] = time.time()
            self._save()
            return self._entries[key]

    def match(self, query: str, *, min_overlap: float = 0.5) -> Optional[dict[str, Any]]:
        """Best template for a goal: exact key first, else Jaccard >= min_overlap."""
        with self._lock:
            self._load()
            key = normalize_goal(query)
            if not key:
                return None
            exact = self._entries.get(key)
            if exact is not None:
                return exact
            query_tokens = _tokens(key)
            candidates = []
            for entry in self._entries.values():
                overlap = _jaccard(query_tokens, _tokens(entry["key"]))
                if overlap >= min_overlap:
                    rank = overlap * 0.7 + float(entry.get("success_rate", 0.0)) * 0.3
                    candidates.append((rank, entry))
            if not candidates:
                return None
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            return candidates[0][1]

    def top(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            self._load()
            return sorted(
                self._entries.values(),
                key=lambda e: (e.get("success_rate", 0.0), e.get("updated_at", 0.0)),
                reverse=True,
            )[: max(limit, 1)]

    def remove(self, query: str) -> bool:
        with self._lock:
            self._load()
            existed = self._entries.pop(normalize_goal(query), None) is not None
            if existed:
                self._save()
            return existed

    def clear(self) -> int:
        with self._lock:
            self._load()
            count = len(self._entries)
            self._entries = {}
            self._save()
            return count

    def __len__(self) -> int:
        with self._lock:
            self._load()
            return len(self._entries)

    def _evict_if_needed(self) -> None:
        if len(self._entries) <= _MAX_ENTRIES:
            return
        oldest = sorted(
            self._entries.values(), key=lambda e: e.get("updated_at", 0.0)
        )[: len(self._entries) - _MAX_ENTRIES]
        for entry in oldest:
            self._entries.pop(entry["key"], None)


_LIBRARY: Optional[PlanLibrary] = None
_LIBRARY_LOCK = threading.Lock()


def get_plan_library() -> PlanLibrary:
    global _LIBRARY
    with _LIBRARY_LOCK:
        if _LIBRARY is None:
            _LIBRARY = PlanLibrary()
        return _LIBRARY


def reset_plan_library() -> None:
    global _LIBRARY
    with _LIBRARY_LOCK:
        _LIBRARY = None


def advise_planner(query: str) -> str:
    """Render a cached template as advisory planner context ('' when none)."""
    entry = get_plan_library().match(query)
    if entry is None:
        return ""
    steps = (entry.get("plan") or {}).get("steps") or []
    lines = [
        "A similar goal succeeded before with this plan template "
        f"(success rate {entry.get('success_rate', 0):.0%} over "
        f"{entry.get('uses', 0)} runs). Treat it as a *starting hypothesis*,",
        "not a script — verify each step against the live application:",
    ]
    for i, step in enumerate(steps[:12], 1):
        tool = step.get("tool") or "reason"
        detail = step.get("description") or ""
        lines.append(f"{i}. [{tool}] {detail}".rstrip())
    return "\n".join(lines)
