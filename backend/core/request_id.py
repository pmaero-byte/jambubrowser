"""Request ID tracking middleware.

Assigns a unique correlation ID to every incoming HTTP request, making it
possible to trace a single request across logs, audit events, and downstream
services.

Behaviour:
  - If the client provides `X-Request-ID`, that value is reused (allows
    distributed tracing where the ID is generated upstream).
  - Otherwise, a new ID is generated (12-char hex).
  - The ID is stored on `scope["request_id"]` and added to response headers.
  - If the same ID is provided more than once in headers, the first wins.
"""
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send


_REQUEST_ID_KEY = "X-Request-ID"
_SCOPE_KEY = "request_id"


def _new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class RequestIDMiddleware:
    """ASGI middleware that tags every request with a correlation ID."""

    def __init__(self, app: ASGIApp, header_name: str = _REQUEST_ID_KEY):
        self.app = app
        self.header_name = header_name.encode("latin-1")

    def _extract_request_id(self, scope: Scope) -> str:
        for name, value in scope.get("headers", []):
            if name.lower() == self.header_name.lower():
                try:
                    decoded = value.decode("latin-1").strip()
                    if decoded and len(decoded) <= 128:
                        return decoded
                except UnicodeDecodeError:
                    pass
                break
        return _new_request_id()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._extract_request_id(scope)
        scope[_SCOPE_KEY] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {h[0].lower() for h in headers}
                if self.header_name.lower() not in existing:
                    headers.append((self.header_name, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def get_request_id(scope: Scope) -> str:
    """Return the request ID stored on the given scope, or empty string."""
    if not scope:
        return ""
    return scope.get(_SCOPE_KEY, "")
