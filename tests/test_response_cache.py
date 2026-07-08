"""Tests for backend.core.response_cache — TTL + LRU + thread-safety."""
from __future__ import annotations

import threading
import time

import pytest

from backend.core.response_cache import CachedResponse, CacheStats, ResponseCache


def _resp(body: bytes = b"hello", status: int = 200, ctype: str = "text/plain") -> CachedResponse:
    return CachedResponse(body=body, status_code=status, content_type=ctype, headers={"x-test": "1"})


class TestBasicGetSet:
    def test_returns_none_on_miss(self):
        cache = ResponseCache()
        assert cache.get("missing") is None

    def test_returns_cached_response_on_hit(self):
        cache = ResponseCache()
        cache.set("k", _resp(b"abc"))
        hit = cache.get("k")
        assert hit is not None
        assert hit.body == b"abc"
        assert hit.status_code == 200
        assert hit.content_type == "text/plain"
        assert hit.headers == {"x-test": "1"}

    def test_set_replaces_existing_entry(self):
        cache = ResponseCache()
        cache.set("k", _resp(b"first"))
        cache.set("k", _resp(b"second"))
        assert cache.get("k").body == b"second"
        assert cache.stats().entries == 1

    def test_delete_removes_entry(self):
        cache = ResponseCache()
        cache.set("k", _resp())
        assert cache.delete("k") is True
        assert cache.get("k") is None
        assert cache.delete("k") is False  # already gone

    def test_clear_empties_the_cache(self):
        cache = ResponseCache()
        cache.set("a", _resp())
        cache.set("b", _resp())
        cache.clear()
        assert cache.stats().entries == 0
        assert cache.get("a") is None


class TestTTL:
    def test_expired_entry_returns_none_and_counts_as_miss(self):
        cache = ResponseCache(max_size_bytes=1024, default_ttl=0.05)  # 50ms
        cache.set("k", _resp())
        assert cache.get("k") is not None
        time.sleep(0.08)
        assert cache.get("k") is None
        stats = cache.stats()
        assert stats.expirations == 1
        assert stats.misses == 1

    def test_per_entry_ttl_override(self):
        cache = ResponseCache(default_ttl=10.0)  # long default
        cache.set("short", _resp(), ttl=0.05)
        cache.set("long", _resp(), ttl=10.0)
        time.sleep(0.08)
        assert cache.get("short") is None
        assert cache.get("long") is not None

    def test_zero_or_negative_ttl_rejected(self):
        cache = ResponseCache()
        with pytest.raises(ValueError):
            cache.set("k", _resp(), ttl=0)
        with pytest.raises(ValueError):
            cache.set("k", _resp(), ttl=-1)


class TestLRUEviction:
    def test_evicts_least_recently_used_when_over_budget(self):
        cache = ResponseCache(max_size_bytes=10, default_ttl=60)
        cache.set("a", _resp(b"12345"))  # 5 bytes
        cache.set("b", _resp(b"67890"))  # 5 bytes (total 10, at budget)
        cache.set("c", _resp(b"ABCDE"))  # 5 bytes -> need to evict 5
        # 'a' was the oldest, should be evicted.
        assert cache.get("a") is None
        assert cache.get("b") is not None
        assert cache.get("c") is not None
        assert cache.stats().evictions == 1

    def test_get_promotes_to_most_recently_used(self):
        cache = ResponseCache(max_size_bytes=10, default_ttl=60)
        cache.set("a", _resp(b"12345"))
        cache.set("b", _resp(b"67890"))
        # Touch 'a' to make it MRU.
        assert cache.get("a") is not None
        cache.set("c", _resp(b"ABCDE"))  # should evict 'b' now
        assert cache.get("a") is not None
        assert cache.get("b") is None
        assert cache.get("c") is not None

    def test_oversized_entry_rejected(self):
        cache = ResponseCache(max_size_bytes=10)
        with pytest.raises(ValueError, match="exceeds cache budget"):
            cache.set("big", _resp(b"x" * 100))


class TestStats:
    def test_initial_stats_are_zero(self):
        cache = ResponseCache()
        s = cache.stats()
        assert s.entries == 0
        assert s.bytes_used == 0
        assert s.hits == 0
        assert s.misses == 0
        assert s.evictions == 0
        assert s.expirations == 0
        assert s.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        cache = ResponseCache()
        cache.set("a", _resp())
        cache.get("a")  # hit
        cache.get("a")  # hit
        cache.get("missing")  # miss
        s = cache.stats()
        assert s.hits == 2
        assert s.misses == 1
        assert s.hit_rate == pytest.approx(2 / 3)

    def test_bytes_used_tracks_stored_size(self):
        cache = ResponseCache()
        cache.set("a", _resp(b"12345"))
        cache.set("b", _resp(b"678"))
        assert cache.stats().bytes_used == 8


class TestValidation:
    def test_invalid_max_size_rejected(self):
        with pytest.raises(ValueError):
            ResponseCache(max_size_bytes=0)
        with pytest.raises(ValueError):
            ResponseCache(max_size_bytes=-1)

    def test_invalid_default_ttl_rejected(self):
        with pytest.raises(ValueError):
            ResponseCache(default_ttl=0)
        with pytest.raises(ValueError):
            ResponseCache(default_ttl=-5)


class TestThreadSafety:
    def test_concurrent_set_and_get_does_not_corrupt(self):
        cache = ResponseCache(max_size_bytes=10_000, default_ttl=60)
        errors: list[Exception] = []

        def writer(start: int) -> None:
            try:
                for i in range(200):
                    cache.set(f"k-{start}-{i}", _resp(b"x" * 10))
            except Exception as e:
                errors.append(e)

        def reader() -> None:
            try:
                for i in range(200):
                    cache.get(f"k-{i % 8}-{i % 200}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(0,)),
            threading.Thread(target=writer, args=(100,)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # We should have written up to 400 distinct keys, budget allows 1000
        # entries of 10 bytes = 10000 bytes, so all 400 fit.
        assert cache.stats().entries == 400
