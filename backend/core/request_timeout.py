"""Request timeout middleware.

Cancels requests whose total processing time exceeds `timeout_seconds`.
Sends a 504 Gateway Timeout response with the request_id (if present) so
clients can correlate with server-side logs.

Use sparingly: applies to ALL routes by default. Some long-running endpoints
(LLM calls, scrape, research) may need to be excluded via the
`exclude_paths` parameter.
"""
import asyncio

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse
from backend.core.security_events import log_security_event, extract_client_ip


_DEFAULT_TIMEOUT = 60.0


class RequestTimeoutMiddleware:
    """ASGI middleware that enforces a per-request processing timeout."""

    def __init__(self, app: ASGIApp, timeout_seconds: float = _DEFAULT_TIMEOUT,
                 exclude_paths: list = None):
        self.app = app
        self.timeout_seconds = timeout_seconds
        self.exclude_paths = list(exclude_paths or ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if any(path.startswith(p) for p in self.exclude_paths):
            await self.app(scope, receive, send)
            return

        timed_out = False

        async def send_wrapper(message):
            nonlocal timed_out
            if timed_out:
                return
            if message["type"] == "http.response.start" and message.get("status", 500) >= 500:
                pass
            await send(message)

        try:
            await asyncio.wait_for(
                self.app(scope, receive, send_wrapper),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            log_security_event(
                action="request_timeout",
                client_ip=extract_client_ip(scope),
                path=path,
                method=scope.get("method", ""),
                request_id=scope.get("request_id", ""),
                details={"timeout_seconds": self.timeout_seconds},
            )
            await self._send_timeout(send, path, scope)

    async def _send_timeout(self, send: Send, path: str, scope: Scope) -> None:
        import json
        request_id = scope.get("request_id", "")
        body = json.dumps({
            "detail": "Request processing timed out",
            "path": path,
            "timeout_seconds": self.timeout_seconds,
        }).encode("utf-8")
        if request_id:
            body_dict = json.loads(body.decode("utf-8"))
            body_dict["request_id"] = request_id
            body = json.dumps(body_dict).encode("utf-8")

        await send({
            "type": "http.response.start",
            "status": 504,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
