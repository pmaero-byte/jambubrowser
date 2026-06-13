"""Security headers middleware for the FastAPI app.

Adds standard security-related response headers to every HTTP response:
- X-Content-Type-Options: nosniff  — prevents MIME-type sniffing
- X-Frame-Options: DENY            — prevents clickjacking
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy: limit features (camera, microphone, geolocation)
- X-XSS-Protection: 0              — disabled in modern browsers (CSP is preferred)
"""
from starlette.types import ASGIApp, Receive, Scope, Send


_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "connect-src 'self' ws: wss: http://localhost:* http://127.0.0.1:*; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)

_DEFAULT_PERMISSIONS = (
    "camera=(), microphone=(), geolocation=(), payment=()"
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": _DEFAULT_PERMISSIONS,
    "Content-Security-Policy": _DEFAULT_CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeadersMiddleware:
    """ASGI middleware that injects standard security headers into every response."""

    def __init__(self, app: ASGIApp, headers: dict = None):
        self.app = app
        self.headers = headers or _SECURITY_HEADERS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing_keys = {h[0].lower() for h in headers}
                for name, value in self.headers.items():
                    if name.lower().encode("latin-1") not in existing_keys:
                        headers.append((name.encode("latin-1"), value.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
