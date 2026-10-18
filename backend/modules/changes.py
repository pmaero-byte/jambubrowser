"""
Change tracking for scraped pages
=================================

"Tell me when this page changes" is a different question from "is this site up"
or "did my audit findings move". The audit monitors watch findings and
screenshots; this watches the *content* of a URL, across runs, durably.

Each check stores a snapshot keyed by URL and compares it to the previous one:

* the **content hash** answers "changed?" cheaply and exactly;
* a **bounded unified diff** answers "how?", which is what a human or an agent
  actually needs to act on.

Persistence is a small pair of SQLite tables, so a watcher set survives a
restart — which is the whole point of a thing that compares today to yesterday.

Honest limit: the diff is line-oriented, so a page that relayouts its HTML on
every render will look changed even when the prose is identical. Normalising
before hashing (markdown, not raw HTML) is the mitigation, and the caller
controls that by choosing what they feed in.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("jambu.changes")

# Bounds so a huge page cannot produce a huge diff payload.
MAX_DIFF_LINES = 200
MAX_STORED_CHARS = 200_000


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def unified_diff(old: str, new: str, *, context: int = 3) -> List[str]:
    """A bounded unified diff between two page texts."""
    if old == new:
        return []
    lines = list(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
            n=context,
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        extra = len(lines) - MAX_DIFF_LINES
        lines = lines[:MAX_DIFF_LINES] + [f"... diff truncated ({extra} more lines)"]
    return lines


def summarise_diff(diff_lines: List[str]) -> Dict[str, int]:
    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    return {"added_lines": added, "removed_lines": removed}


@dataclass
class ChangeDecision:
    """What happened when a URL was checked."""

    url: str
    changed: bool
    is_first_check: bool
    hash_before: Optional[str]
    hash_after: str
    checked_at: float
    added_lines: int = 0
    removed_lines: int = 0
    diff: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.diff is None:
            self.diff = []

    def to_dict(self, *, include_diff: bool = True) -> Dict[str, object]:
        out: Dict[str, object] = {
            "url": self.url,
            "changed": self.changed,
            "first_check": self.is_first_check,
            "hash_before": self.hash_before,
            "hash_after": self.hash_after,
            "checked_at": self.checked_at,
            "added_lines": self.added_lines,
            "removed_lines": self.removed_lines,
        }
        if include_diff:
            out["diff"] = self.diff
        return out


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def get_snapshot(url: str) -> Optional[Dict[str, object]]:
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT url, content_sha256, text, bytes, first_seen, last_checked, "
            "last_changed, check_count, change_count FROM page_snapshots WHERE url = ?",
            (url,),
        ).fetchone()
    if not row:
        return None
    keys = ("url", "content_sha256", "text", "bytes", "first_seen",
            "last_checked", "last_changed", "check_count", "change_count")
    return dict(zip(keys, row))


def list_watched(limit: int = 200) -> List[Dict[str, object]]:
    from backend.core.database import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT url, content_sha256, bytes, first_seen, last_checked, "
            "last_changed, check_count, change_count FROM page_snapshots "
            "ORDER BY last_checked DESC LIMIT ?",
            (limit,),
        ).fetchall()
    keys = ("url", "content_sha256", "bytes", "first_seen", "last_checked",
            "last_changed", "check_count", "change_count")
    return [dict(zip(keys, r)) for r in rows]


def forget(url: str) -> bool:
    from backend.core.database import get_db

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM page_snapshots WHERE url = ?", (url,))
        # Capture this before the second DELETE: `rowcount` reflects the *last*
        # statement executed, so reading it afterwards reported "not found" for
        # any URL that had no change history.
        existed = cur.rowcount > 0
        cur.execute("DELETE FROM page_changes WHERE url = ?", (url,))
        conn.commit()
        return existed


def recent_changes(url: Optional[str] = None, limit: int = 50) -> List[Dict[str, object]]:
    from backend.core.database import get_db

    with get_db() as conn:
        if url:
            rows = conn.execute(
                "SELECT url, changed_at, hash_before, hash_after, added_lines, "
                "removed_lines FROM page_changes WHERE url = ? "
                "ORDER BY changed_at DESC LIMIT ?",
                (url, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT url, changed_at, hash_before, hash_after, added_lines, "
                "removed_lines FROM page_changes ORDER BY changed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    keys = ("url", "changed_at", "hash_before", "hash_after", "added_lines", "removed_lines")
    return [dict(zip(keys, r)) for r in rows]


def record_check(url: str, text: str, *, now: Optional[float] = None) -> ChangeDecision:
    """Compare ``text`` to the stored snapshot, persist, and report the delta.

    The first check for a URL is never reported as a "change" — there is nothing
    to compare against, and alerting on it would fire once for every new URL.
    """
    from backend.core.database import get_db

    now = now if now is not None else time.time()
    text = text or ""
    new_hash = content_hash(text)
    previous = get_snapshot(url)

    if previous is None:
        decision = ChangeDecision(
            url=url, changed=False, is_first_check=True,
            hash_before=None, hash_after=new_hash, checked_at=now,
        )
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO page_snapshots "
                "(url, content_sha256, text, bytes, first_seen, last_checked, "
                " last_changed, check_count, change_count) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 1, 0)",
                (url, new_hash, text[:MAX_STORED_CHARS], len(text.encode("utf-8", "replace")), now, now),
            )
            conn.commit()
        return decision

    old_hash = str(previous["content_sha256"])
    changed = old_hash != new_hash
    diff_lines: List[str] = []
    stats = {"added_lines": 0, "removed_lines": 0}

    if changed:
        diff_lines = unified_diff(str(previous.get("text") or ""), text)
        stats = summarise_diff(diff_lines)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE page_snapshots SET content_sha256 = ?, text = ?, bytes = ?, "
            "last_checked = ?, last_changed = COALESCE(?, last_changed), "
            "check_count = check_count + 1, "
            "change_count = change_count + ? WHERE url = ?",
            (
                new_hash, text[:MAX_STORED_CHARS], len(text.encode("utf-8", "replace")),
                now, now if changed else None, 1 if changed else 0, url,
            ),
        )
        if changed:
            cur.execute(
                "INSERT INTO page_changes (url, changed_at, hash_before, hash_after, "
                "added_lines, removed_lines) VALUES (?, ?, ?, ?, ?, ?)",
                (url, now, old_hash, new_hash, stats["added_lines"], stats["removed_lines"]),
            )
        conn.commit()

    return ChangeDecision(
        url=url, changed=changed, is_first_check=False,
        hash_before=old_hash, hash_after=new_hash, checked_at=now,
        added_lines=stats["added_lines"], removed_lines=stats["removed_lines"],
        diff=diff_lines,
    )
