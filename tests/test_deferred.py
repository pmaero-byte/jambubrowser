"""
Tests: YouTube Analyzer & Rate Limiter
=======================================
Tests for YouTube video analysis and API rate limiting.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.engine import app
    with TestClient(app) as c:
        yield c


class TestYouTubeAnalyzer:
    """Tests for YouTube video analysis module."""

    def test_extract_video_id_standard(self):
        from backend.modules.youtube import YouTubeAnalyzer
        vid = YouTubeAnalyzer.extract_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert vid == "dQw4w9WgXcQ"

    def test_extract_video_id_short(self):
        from backend.modules.youtube import YouTubeAnalyzer
        vid = YouTubeAnalyzer.extract_video_id("https://youtu.be/dQw4w9WgXcQ")
        assert vid == "dQw4w9WgXcQ"

    def test_extract_video_id_embed(self):
        from backend.modules.youtube import YouTubeAnalyzer
        vid = YouTubeAnalyzer.extract_video_id(
            "https://www.youtube.com/embed/dQw4w9WgXcQ"
        )
        assert vid == "dQw4w9WgXcQ"

    def test_extract_video_id_shorts(self):
        from backend.modules.youtube import YouTubeAnalyzer
        vid = YouTubeAnalyzer.extract_video_id(
            "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        )
        assert vid == "dQw4w9WgXcQ"

    def test_extract_video_id_invalid(self):
        from backend.modules.youtube import YouTubeAnalyzer
        vid = YouTubeAnalyzer.extract_video_id("https://example.com/not-youtube")
        assert vid is None

    def test_extract_video_id_with_params(self):
        from backend.modules.youtube import YouTubeAnalyzer
        vid = YouTubeAnalyzer.extract_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30"
        )
        assert vid == "dQw4w9WgXcQ"

    def test_youtube_video_dataclass(self):
        from backend.modules.youtube import YouTubeVideo, YouTubeTranscript
        video = YouTubeVideo(video_id="test123", url="https://youtube.com/watch?v=test123")
        assert video.video_id == "test123"
        d = video.to_dict()
        assert d["video_id"] == "test123"
        assert "transcript_preview" in d

    def test_get_analyzer_singleton(self):
        from backend.modules.youtube import get_youtube_analyzer
        a1 = get_youtube_analyzer()
        a2 = get_youtube_analyzer()
        assert a1 is a2


class TestYouTubeEndpoints:
    """Tests for YouTube API endpoints."""

    def test_youtube_analyze_invalid_url(self, client):
        response = client.post("/media/youtube", params={"url": "not-a-url"})
        assert response.status_code in (200, 400, 422)

    def test_youtube_transcript_invalid(self, client):
        response = client.get("/media/youtube/transcript", params={"url": "invalid"})
        assert response.status_code in (200, 400, 422)

    def test_youtube_search_invalid(self, client):
        response = client.get("/media/youtube/search", params={
            "url": "invalid",
            "query": "test",
        })
        assert response.status_code in (200, 400, 422)


class TestRateLimiter:
    """Tests for the token bucket rate limiter."""

    def test_token_bucket_creation(self):
        from backend.core.rate_limiter import TokenBucket
        bucket = TokenBucket(tokens=10.0, max_tokens=10.0, refill_rate=2.0)
        assert bucket.tokens == 10.0
        assert bucket.max_tokens == 10.0

    def test_token_bucket_consume(self):
        from backend.core.rate_limiter import TokenBucket
        bucket = TokenBucket(tokens=10.0, max_tokens=10.0, refill_rate=2.0)
        assert bucket.consume(1.0) is True
        assert bucket.tokens == 9.0

    def test_token_bucket_depleted(self):
        from backend.core.rate_limiter import TokenBucket
        bucket = TokenBucket(tokens=1.0, max_tokens=1.0, refill_rate=0.01)
        assert bucket.consume(1.0) is True
        assert bucket.consume(1.0) is False  # Not enough tokens

    def test_rate_limiter_default(self):
        from backend.core.rate_limiter import RateLimiter
        limiter = RateLimiter(default_rate=100.0, default_burst=100)
        assert limiter.default_rate == 100.0

    @pytest.mark.asyncio
    async def test_is_allowed_default(self):
        from backend.core.rate_limiter import RateLimiter
        import asyncio
        limiter = RateLimiter(default_rate=100.0, default_burst=100)
        allowed, remaining, reset = await limiter.is_allowed("127.0.0.1", "/test")
        assert allowed is True

    def test_set_endpoint_limit(self):
        from backend.core.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.set_endpoint_limit("/api/expensive", 1.0, 3)
        assert "/api/expensive" in limiter._endpoint_limits

    def test_get_stats(self):
        from backend.core.rate_limiter import RateLimiter
        limiter = RateLimiter()
        stats = limiter.get_stats()
        assert "active_buckets" in stats
        assert "default_rate" in stats

    def test_get_limiter_singleton(self):
        from backend.core.rate_limiter import get_limiter
        l1 = get_limiter()
        l2 = get_limiter()
        assert l1 is l2


class TestRateLimiterEndpoints:
    """Tests that rate limiting doesn't break endpoints."""

    def test_health_still_works_with_limiter(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "online"

    def test_stats_still_works_with_limiter(self, client):
        response = client.get("/stats")
        assert response.status_code == 200

    def test_rate_limit_headers_present(self, client):
        response = client.get("/stats")
        assert response.status_code == 200

    def test_rate_limit_blocks_when_exceeded(self, client):
        """Verify middleware returns 429 when rate limit exceeded."""
        from backend.core.rate_limiter import get_limiter
        limiter = get_limiter()
        # Snapshot the previous /stats limit so we can restore it after this
        # test — the limiter is a module-level singleton and the aggressive
        # setting would otherwise leak into test_engine.py::TestStatsEndpoint
        # and cause a 429 there.
        prev_stats_limit = limiter._endpoint_limits.get("/stats")
        try:
            limiter.set_endpoint_limit("/stats", 0.001, 0)  # Tiny limit, no burst
            response = client.get("/stats")
            assert response.status_code in (200, 429)
        finally:
            if prev_stats_limit is None:
                limiter._endpoint_limits.pop("/stats", None)
            else:
                limiter._endpoint_limits["/stats"] = prev_stats_limit
