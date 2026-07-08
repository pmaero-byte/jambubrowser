"""Thread-safe in-memory log of proxy requests.

Records every request the browser proxy serves so the DevTools panel
can show what the browser fetched, whether each came from cache, and
how long it took. Bounded to a configurable entry cap so long-running
processes do not leak memory.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ProxyLogEntry:
    """One row in the proxy request log."""
    timestamp: float
    url: str
    method: str
    status_code: int
    cache_hit: bool
    duration_ms: float
    content_length: int
    content_type: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class ProxyRequestLog:
    """Bounded ring buffer of recent proxy requests."""

    def __init__(self, max_entries: int = 500):
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max = max_entries
        self._entries: deque[ProxyLogEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._total_hits = 0
        self._total_misses = 0
        self._total_errors = 0

    def record(
        self,
        url: str,
        *,
        method: str = "GET",
        status_code: int = 0,
        cache_hit: bool = False,
        duration_ms: float = 0.0,
        content_length: int = 0,
        content_type: str = "",
        error: Optional[str] = None,
    ) -> ProxyLogEntry:
        entry = ProxyLogEntry(
            timestamp=time.time(),
            url=url,
            method=method,
            status_code=status_code,
            cache_hit=cache_hit,
            duration_ms=duration_ms,
            content_length=content_length,
            content_type=content_type,
            error=error,
        )
        with self._lock:
            self._entries.append(entry)
            if cache_hit:
                self._total_hits += 1
            else:
                self._total_misses += 1
            if error or (status_code >= 500 and status_code > 0):
                self._total_errors += 1
        return entry

    def recent(self, limit: int = 50) -> list[ProxyLogEntry]:
        if limit < 1:
            return []
        with self._lock:
            return list(self._entries)[-limit:]

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._total_hits = 0
            self._total_misses = 0
            self._total_errors = 0
        return count

    def stats(self) -> dict:
        with self._lock:
            total = len(self._entries)
            return {
                "total_logged": total,
                "cache_hits": self._total_hits,
                "cache_misses": self._total_misses,
                "errors": self._total_errors,
                "capacity": self._max,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_log_instance: Optional[ProxyRequestLog] = None


def get_proxy_log() -> ProxyRequestLog:
    """Return the process-wide ProxyRequestLog singleton."""
    global _log_instance
    if _log_instance is None:
        _log_instance = ProxyRequestLog()
    return _log_instance


def reset_proxy_log() -> None:
    """Drop the cached log (for tests)."""
    global _log_instance
    _log_instance = None
