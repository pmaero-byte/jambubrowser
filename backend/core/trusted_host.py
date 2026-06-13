"""Trusted host middleware.

Rejects HTTP requests whose `Host` header is not in the allow-list. Protects
against:
  - DNS rebinding: an attacker-controlled DNS record points a public hostname
    at a private IP, then a victim triggers a request to that hostname.
  - Host header injection: an attacker forges the Host header to make the
    server believe a request came from a trusted origin.

Configuration via the `ALLOWED_HOSTS` env var (comma-separated). Falls back
to a development-friendly localhost allow-list when unset.
"""
import os
from urllib.parse import unquote

from starlette.types import ASGIApp, Receive, Scope, Send
from backend.core.security_events import log_security_event, extract_client_ip


_DEFAULT_DEV_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "testserver",
    "testclient",
}


def _load_allowed_hosts() -> set:
    env = os.environ.get("ALLOWED_HOSTS", "").strip()
    if not env:
        return set(_DEFAULT_DEV_HOSTS)
    return {h.strip().lower() for h in env.split(",") if h.strip()}


def _strip_port(host_header: str) -> str:
    """Strip port and IPv6 brackets from a Host header value."""
    if not host_header:
        return ""
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        if end != -1:
            return h[: end + 1]
    if ":" in h and not h.startswith("["):
        return h.split(":", 1)[0]
    return h


class TrustedHostMiddleware:
    """ASGI middleware that rejects requests with untrusted `Host` headers."""

    def __init__(self, app: ASGIApp, allowed_hosts=None):
        self.app = app
        self.allowed_hosts = set(h.lower() for h in allowed_hosts) if allowed_hosts else _load_allowed_hosts()

    def _extract_host(self, scope: Scope) -> str:
        for name, value in scope.get("headers", []):
            if name.lower() == b"host":
                return value.decode("latin-1")
        return ""

    def _is_trusted(self, host_header: str) -> bool:
        if not host_header:
            return False
        host = _strip_port(host_header)
        if not host:
            return False
        if host in self.allowed_hosts:
            return True
        if host.startswith("localhost"):
            return True
        if host in {"127.0.0.1", "::1"}:
            return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        host_header = self._extract_host(scope)
        if not self._is_trusted(host_header):
            log_security_event(
                action="untrusted_host",
                client_ip=extract_client_ip(scope),
                path=scope.get("path", ""),
                method=scope.get("method", ""),
                request_id=scope.get("request_id", ""),
                details={"host_header": host_header},
            )
            await self._reject(send, host_header)
            return

        await self.app(scope, receive, send)

    async def _reject(self, send: Send, host_header: str) -> None:
        import json
        body = json.dumps({
            "detail": "Untrusted Host header",
            "host": host_header,
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 421,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
