"""
In-memory HTTP response cache with TTL + LRU eviction.

A small, thread-safe utility designed for the proxy endpoint and any other
read-heavy HTTP path (audit telemetry fetches, risk-shield lookups, etc.)
that wants to avoid re-hitting upstream on every call.

Design notes
------------
* Bounded by total byte size, not entry count — a single 50 MB PDF should
  not push out 500 small JSON responses.
* LRU eviction (oldest-accessed entry) once the byte budget is exceeded.
* Per-entry TTL (defaults to the cache's default_ttl). Expired entries
  are purged lazily on access.
* Thread-safe via a single RLock — async callers must `await` between
  calls but cannot be interrupted mid-operation.
* Pure stdlib — no Redis, no external cache.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CachedResponse:
    """The minimum we need to faithfully replay an HTTP response."""

    body: bytes
    content_type: str
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class CacheStats:
    """Snapshot of cache health — useful for /stats endpoints and tests."""

    entries: int
    bytes_used: int
    bytes_budget: int
    hits: int
    misses: int
    evictions: int
    expirations: int

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class ResponseCache:
    """
    A bounded, TTL-aware, LRU HTTP response cache.

    Example
    -------
    cache = ResponseCache(max_size_bytes=10 * 1024 * 1024, default_ttl=60)

    # Try to serve from cache
    hit = cache.get("GET https://example.com/api")
    if hit is None:
        hit = await fetch_from_upstream()
        cache.set("GET https://example.com/api", hit)

    # Render
    return Response(hit.body, status_code=hit.status_code,
                    media_type=hit.content_type, headers=hit.headers)
    """

    def __init__(self, max_size_bytes: int = 50 * 1024 * 1024, default_ttl: float = 60.0):
        if max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be positive")
        if default_ttl <= 0:
            raise ValueError("default_ttl must be positive")
        self._max_bytes = max_size_bytes
        self._default_ttl = default_ttl
        # key -> (expires_at, size_bytes, response)
        self._entries: "OrderedDict[str, tuple[float, int, CachedResponse]]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0

    @property
    def default_ttl(self) -> float:
        return self._default_ttl

    def get(self, key: str) -> Optional[CachedResponse]:
        """Return a fresh cached response, or None on miss / expiry."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, _size, response = entry
            if expires_at <= time.monotonic():
                # Expired — drop and count as a miss.
                del self._entries[key]
                self._expirations += 1
                self._misses += 1
                return None
            # Mark as recently used.
            self._entries.move_to_end(key)
            self._hits += 1
            return response

    def set(self, key: str, response: CachedResponse, ttl: Optional[float] = None) -> None:
        """Store a response, evicting older entries if necessary."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        if effective_ttl <= 0:
            raise ValueError("ttl must be positive")
        size = len(response.body)
        if size > self._max_bytes:
            # Single entry larger than the entire budget — refuse rather
            # than thrash. Caller can catch and re-fetch.
            raise ValueError(
                f"Response body ({size} bytes) exceeds cache budget ({self._max_bytes} bytes)"
            )
        with self._lock:
            # Replace an existing entry for this key.
            existing = self._entries.pop(key, None)
            if existing is not None:
                _old_expires, old_size, _ = existing
                # Net size delta: subtract the old size.
                self._make_room_for(size - old_size)
            else:
                self._make_room_for(size)
            self._entries[key] = (time.monotonic() + effective_ttl, size, response)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> CacheStats:
        with self._lock:
            used = sum(size for _exp, size, _r in self._entries.values())
            return CacheStats(
                entries=len(self._entries),
                bytes_used=used,
                bytes_budget=self._max_bytes,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                expirations=self._expirations,
            )

    def _make_room_for(self, needed: int) -> None:
        """Evict LRU entries until `needed` bytes are available."""
        used = sum(size for _exp, size, _r in self._entries.values())
        while self._entries and used + needed > self._max_bytes:
            _old_key, (_old_exp, old_size, _old_resp) = self._entries.popitem(last=False)
            used -= old_size
            self._evictions += 1
