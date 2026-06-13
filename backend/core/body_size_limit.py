"""Request body size limit middleware.

Rejects HTTP requests with bodies larger than `max_bytes`. Protects against
DoS attacks via large payloads (e.g. JSON bombs, oversized form data).

Checks the `Content-Length` header first when present (cheapest path) and
then enforces the limit on the actual body for chunked transfer encoding
where Content-Length may be missing.
"""
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse
from backend.core.security_events import log_security_event, extract_client_ip


_DEFAULT_MAX_BYTES = 1 * 1024 * 1024


class BodySizeLimitMiddleware:
    """ASGI middleware that rejects requests whose body exceeds `max_bytes`."""

    def __init__(self, app: ASGIApp, max_bytes: int = _DEFAULT_MAX_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._content_length_exceeds_limit(scope):
            log_security_event(
                action="body_too_large",
                client_ip=extract_client_ip(scope),
                path=scope.get("path", ""),
                method=scope.get("method", ""),
                request_id=scope.get("request_id", ""),
                details={"limit": self.max_bytes},
            )
            await self._reject(send, "Request body too large")
            return

        total = 0
        over_limit = False

        async def wrapped_receive():
            nonlocal total, over_limit
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total += len(body)
                if total > self.max_bytes:
                    over_limit = True
            return message

        async def wrapped_send(message):
            if over_limit:
                return
            await send(message)

        if over_limit:
            log_security_event(
                action="body_too_large",
                client_ip=extract_client_ip(scope),
                path=scope.get("path", ""),
                method=scope.get("method", ""),
                request_id=scope.get("request_id", ""),
                details={"limit": self.max_bytes},
            )
            await self._reject(send, "Request body too large")
            return

        await self.app(scope, wrapped_receive, wrapped_send)

    def _content_length_exceeds_limit(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    length = int(value.decode("latin-1"))
                    return length > self.max_bytes
                except (ValueError, UnicodeDecodeError):
                    return True
        return False

    async def _reject(self, send: Send, detail: str) -> None:
        import json
        body = json.dumps({"detail": detail, "max_bytes": self.max_bytes}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
