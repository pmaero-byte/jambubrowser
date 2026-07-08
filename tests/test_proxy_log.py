"""Tests: backend/core/proxy_log.py — in-memory proxy request log."""
import pytest


@pytest.fixture(autouse=True)
def reset_log():
    from backend.core.proxy_log import reset_proxy_log
    reset_proxy_log()
    yield
    reset_proxy_log()


class TestProxyRequestLog:
    def test_record_and_recent(self):
        from backend.core.proxy_log import ProxyRequestLog
        log_ = ProxyRequestLog(max_entries=10)
        log_.record("https://example.com", status_code=200, cache_hit=False, duration_ms=12.0, content_length=500)
        log_.record("https://example.com/2", status_code=200, cache_hit=True, duration_ms=1.0, content_length=500)
        recent = log_.recent()
        assert len(recent) == 2
        assert recent[0].url == "https://example.com"
        assert recent[1].cache_hit is True

    def test_recent_respects_limit(self):
        from backend.core.proxy_log import ProxyRequestLog
        log_ = ProxyRequestLog(max_entries=10)
        for i in range(5):
            log_.record(f"https://e.com/{i}", status_code=200)
        assert len(log_.recent(limit=2)) == 2
        assert len(log_.recent(limit=0)) == 0

    def test_bounded_ring_buffer(self):
        from backend.core.proxy_log import ProxyRequestLog
        log_ = ProxyRequestLog(max_entries=3)
        for i in range(5):
            log_.record(f"https://e.com/{i}", status_code=200)
        assert len(log_) == 3
        recent = log_.recent()
        # Oldest entries get evicted; newest survive
        assert recent[0].url == "https://e.com/2"
        assert recent[-1].url == "https://e.com/4"

    def test_clear(self):
        from backend.core.proxy_log import ProxyRequestLog
        log_ = ProxyRequestLog()
        log_.record("https://e.com", status_code=200)
        log_.record("https://e.com/2", status_code=500, error="boom")
        cleared = log_.clear()
        assert cleared == 2
        assert len(log_) == 0
        assert log_.stats()["cache_hits"] == 0
        assert log_.stats()["errors"] == 0

    def test_stats_counts(self):
        from backend.core.proxy_log import ProxyRequestLog
        log_ = ProxyRequestLog()
        log_.record("https://e.com/1", status_code=200, cache_hit=True)
        log_.record("https://e.com/2", status_code=200, cache_hit=False)
        log_.record("https://e.com/3", status_code=500, error="boom")
        s = log_.stats()
        assert s["cache_hits"] == 1
        assert s["cache_misses"] == 2
        assert s["errors"] == 1

    def test_invalid_max_entries(self):
        from backend.core.proxy_log import ProxyRequestLog
        with pytest.raises(ValueError):
            ProxyRequestLog(max_entries=0)

    def test_to_dict_round_trip(self):
        from backend.core.proxy_log import ProxyRequestLog
        log_ = ProxyRequestLog()
        entry = log_.record("https://e.com", status_code=200, cache_hit=True, duration_ms=5.0, content_length=100, content_type="text/html")
        d = entry.to_dict()
        assert d["url"] == "https://e.com"
        assert d["status_code"] == 200
        assert d["cache_hit"] is True
        assert d["duration_ms"] == 5.0
        assert d["content_length"] == 100
        assert d["content_type"] == "text/html"

    def test_singleton(self):
        from backend.core.proxy_log import get_proxy_log
        a = get_proxy_log()
        b = get_proxy_log()
        assert a is b


class TestDevtoolsProxyLogEndpoint:
    def test_returns_entries(self):
        from backend.core.proxy_log import get_proxy_log
        from backend.routes.proxy import devtools_proxy_log
        log_ = get_proxy_log()
        log_.record("https://e.com/a", status_code=200, cache_hit=False, duration_ms=10.0, content_length=500)
        log_.record("https://e.com/b", status_code=200, cache_hit=True, duration_ms=1.0, content_length=500)
        import asyncio
        result = asyncio.run(devtools_proxy_log(limit=10))
        assert result["count"] == 2
        assert result["stats"]["total_logged"] == 2
        assert result["stats"]["cache_hits"] == 1
        assert result["stats"]["cache_misses"] == 1
        assert result["entries"][0]["url"] == "https://e.com/a"

    def test_rejects_invalid_limit(self):
        from backend.routes.proxy import devtools_proxy_log
        import asyncio
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            asyncio.run(devtools_proxy_log(limit=0))
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            asyncio.run(devtools_proxy_log(limit=1000))
        assert exc.value.status_code == 400

    def test_clear_endpoint(self):
        from backend.core.proxy_log import get_proxy_log
        from backend.routes.proxy import devtools_proxy_log_clear
        log_ = get_proxy_log()
        log_.record("https://e.com", status_code=200)
        log_.record("https://e.com/2", status_code=200)
        import asyncio
        result = asyncio.run(devtools_proxy_log_clear())
        assert result["cleared"] == 2
        assert len(log_) == 0
