"""Access logging middleware.

Records a single structured log line per HTTP request:
  method, path, status, duration_ms, client_ip, request_id.

Skips noisy paths (health, websocket upgrades) by default to keep logs clean.
Designed to run as one of the outermost middlewares so it captures the
final status code after all other middleware has processed the request.
"""
import logging
import time

from starlette.types import ASGIApp, Receive, Scope, Send
from backend.core.security_events import extract_client_ip


log = logging.getLogger("jambu.access")


_DEFAULT_SKIP_PATHS = {"/health", "/favicon.ico"}


class AccessLogMiddleware:
    """ASGI middleware that emits one log line per HTTP request."""

    def __init__(self, app: ASGIApp, skip_paths=None, slow_threshold_ms: float = 1000.0):
        self.app = app
        self.skip_paths = set(skip_paths) if skip_paths else set(_DEFAULT_SKIP_PATHS)
        self.slow_threshold_ms = slow_threshold_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if path in self.skip_paths:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        client_ip = extract_client_ip(scope)
        request_id = scope.get("request_id", "")
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            status = status_holder["status"]
            extra = "SLOW" if duration_ms > self.slow_threshold_ms else "OK"
            log.info(
                "%s %s %d %.1fms ip=%s rid=%s [%s]",
                method, path, status, duration_ms, client_ip, request_id, extra,
            )
