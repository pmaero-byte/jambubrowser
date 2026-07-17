"""
Rate Limiter Middleware
========================
Token bucket rate limiting for API endpoints.
Configurable per-endpoint or global rate limits.

Features:
- Token bucket algorithm
- Per-IP and per-endpoint tracking
- Configurable burst and refill rates
- FastAPI middleware integration
- Rate limit headers in responses
"""

import asyncio
import time
from typing import Optional, Dict, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from backend.core.security_events import log_security_event


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    tokens: float
    max_tokens: float
    refill_rate: float  # tokens per second
    last_refill: float = field(default_factory=time.time)

    def consume(self, tokens: float = 1.0) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        now = time.time()

        # Refill tokens based on elapsed time
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """
    Configurable rate limiter using token bucket algorithm.
    Supports per-endpoint and per-client rate limits.
    """

    def __init__(self, default_rate: float = 60.0, default_burst: int = 120):
        """
        Args:
            default_rate: Default requests per second refill rate
            default_burst: Default maximum burst size (bucket capacity)
        """
        self.default_rate = default_rate
        self.default_burst = default_burst
        self._buckets: Dict[str, TokenBucket] = {}
        self._endpoint_limits: Dict[str, Tuple[float, int]] = {}
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def set_endpoint_limit(self, path: str, rate: float, burst: int):
        """Set a custom rate limit for a specific endpoint path."""
        self._endpoint_limits[path] = (rate, burst)

    def _get_bucket_key(self, client_ip: str, path: str) -> str:
        return f"{client_ip}:{path}"

    def _get_or_create_bucket(self, key: str, rate: float, 
                                burst: int) -> TokenBucket:
        if key not in self._buckets:
            # Clean up old buckets periodically
            if len(self._buckets) > 10000:
                self._cleanup()

            self._buckets[key] = TokenBucket(
                tokens=float(burst),
                max_tokens=float(burst),
                refill_rate=rate,
            )
        return self._buckets[key]

    def _cleanup(self):
        """Remove expired bucket entries."""
        now = time.time()
        expired = [
            k for k, b in self._buckets.items()
            if now - b.last_refill > 3600  # 1 hour inactive
        ]
        for k in expired[:1000]:
            del self._buckets[k]

    async def is_allowed(self, client_ip: str, path: str,
                         method: str = "GET") -> Tuple[bool, float, float]:
        """
        Check if a request is allowed under the rate limit.

        Returns:
            (allowed, remaining_tokens, reset_time_seconds)
        """
        async with self._get_lock():
            # Check for endpoint-specific limit (path prefix match)
            for ep_path, (rate, burst) in self._endpoint_limits.items():
                if path.startswith(ep_path):
                    key = self._get_bucket_key(client_ip, ep_path)
                    bucket = self._get_or_create_bucket(key, rate, burst)
                    allowed = bucket.consume()
                    return (allowed, bucket.tokens, 
                            (bucket.max_tokens - bucket.tokens) / rate if rate > 0 else 0)

            # Method-aware default: POST/PUT/DELETE tighter than GET/HEAD
            if method in ("POST", "PUT", "DELETE", "PATCH"):
                default_key = "default_post"
                # Use the stored default_post limit if set, otherwise fall back
                rate, burst = self._endpoint_limits.get(default_key,
                    (self.default_rate / 2, self.default_burst / 2))
            else:
                rate, burst = self.default_rate, self.default_burst

            key = self._get_bucket_key(client_ip, default_key if method in ("POST", "PUT", "DELETE", "PATCH") else "default")
            bucket = self._get_or_create_bucket(key, rate, burst)
            allowed = bucket.consume()
            return (allowed, bucket.tokens,
                    (bucket.max_tokens - bucket.tokens) / rate if rate > 0 else 0)

    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            'active_buckets': len(self._buckets),
            'default_rate': self.default_rate,
            'default_burst': self.default_burst,
            'endpoint_limits': len(self._endpoint_limits),
        }

    def reset(self) -> None:
        """Clear all per-client buckets. Used by tests to start each test
        with a fresh token budget — otherwise the module-level singleton
        leaks tokens consumed by earlier tests, and later tests get 429s.
        """
        self._buckets.clear()


class RateLimitMiddleware:
    """
    Pure ASGI middleware for rate limiting.
    Avoids BaseHTTPMiddleware issues with streaming/background tasks.
    """

    def __init__(self, app, limiter: RateLimiter = None):
        self.app = app
        self.limiter = limiter or RateLimiter()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if path in ("/health", "/ws") or scope.get("type") == "websocket":
            await self.app(scope, receive, send)
            return

        client_ip = scope.get("client", ("unknown", 0))[0]
        method = scope.get("method", "GET")
        allowed, remaining, reset_time = await self.limiter.is_allowed(client_ip, path, method)

        if not allowed:
            log_security_event(
                action="rate_limit_exceeded",
                client_ip=client_ip,
                path=path,
                method=method,
            )
            body = b'{"error":"Rate limit exceeded. Try again later."}'
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-ratelimit-limit", str(int(self.limiter.default_burst)).encode()),
                    (b"x-ratelimit-remaining", b"0"),
                    (b"retry-after", str(int(reset_time)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(int(self.limiter.default_burst)).encode()))
                headers.append((b"x-ratelimit-remaining", str(int(remaining)).encode()))
                headers.append((b"x-ratelimit-reset", str(int(reset_time)).encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


# Module-level singleton
_limiter: Optional[RateLimiter] = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
