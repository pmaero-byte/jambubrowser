"""
Remote MCP server — Streamable HTTP transport with token auth.

`backend/mcp_server.py` is the stdio server (Claude Desktop / Cursor local
install). This module exposes the same 28-tool surface over **Streamable
HTTP** (the 2026 MCP transport; SSE is deprecated) so remote clients and
hosted agents can connect without installing anything:

- ``POST /mcp/``            — MCP Streamable HTTP endpoint (auth required)
- ``GET  /health``          — liveness (public)
- ``GET  /.well-known/mcp-server-card.json`` — Server Card (public)

Auth is **Bearer token or X-API-Key**: either the static
``JAMBU_MCP_TOKEN`` (dev/single-user) or any active engine API key from
the ``api_keys`` table (``jambu_…``, created via ``POST /api-keys/create``).
Remote MCP servers are scanned for missing auth in the wild — this one
refuses to serve tools without a token.

Not browser-facing: no CORS headers are emitted, and the card advertises
that. Deploy behind HTTPS (reverse proxy) for non-localhost use.

Standalone:

    python -m uvicorn backend.mcp_http:app --host 127.0.0.1 --port 8765

Mounted in the engine at ``/mcp`` (single port, existing middleware stack).
"""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

log = logging.getLogger("jambu.mcp.http")

# Paths served without auth (no data, no tools).
PUBLIC_PATHS = {
    "/health",
    "/.well-known/mcp-server-card.json",
    "/server-card.json",
}

DEFAULT_DESCRIPTION = (
    "Audit web apps, drive a real browser, run mesh compute — 28 MCP tools."
)


# ---------------------------------------------------------------------------
# Tool profile
# ---------------------------------------------------------------------------

def active_profile() -> str:
    """``JAMBU_MCP_PROFILE`` = full (default) | curated.

    Curated keeps the surface compact for better tool selection and drops
    the arbitrary-execution tool (``execute_tool``), which remote callers
    should opt into explicitly via ``full``.
    """
    profile = (os.environ.get("JAMBU_MCP_PROFILE") or "full").strip().lower()
    return profile if profile in ("full", "curated") else "full"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _static_token() -> str:
    return (os.environ.get("JAMBU_MCP_TOKEN") or "").strip()


def check_token(token: Optional[str]) -> bool:
    """True when a token is the static MCP token or an active API key."""
    if not token:
        return False
    static = _static_token()
    if static and hmac.compare_digest(token, static):
        return True
    try:
        from backend.core.api_keys import validate_api_key

        return validate_api_key(token) is not None
    except Exception:  # DB unavailable → static token is the only path
        log.warning("API-key validation failed", exc_info=True)
        return False


def _extract_token(scope: dict) -> Optional[str]:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key") or None


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {
            "error": "unauthorized",
            "detail": (
                "Remote MCP requires a token: send 'Authorization: Bearer <key>' "
                "(engine API key from POST /api-keys/create, or JAMBU_MCP_TOKEN)."
            ),
        },
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="jambubrowser-mcp"'},
    )


class McpAuthMiddleware:
    """Raw ASGI auth gate (MCP streams responses — no body buffering)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "/")
        if path in PUBLIC_PATHS or path.startswith("/.well-known/"):
            return await self.app(scope, receive, send)
        if not check_token(_extract_token(scope)):
            return await _unauthorized()(scope, receive, send)
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------

def _configured_mcp():
    """The shared FastMCP instance with the remote transport settings."""
    from backend.mcp_server import mcp

    mcp.settings.streamable_http_path = "/"
    # Stateless requests scale horizontally and need no session store; the
    # 2026 roadmap explicitly targets stateless transport.
    mcp.settings.stateless_http = True
    mcp.settings.json_response = False
    return mcp


def mcp_asgi_app():
    """Authed Streamable-HTTP MCP app (endpoint at ``/``)."""
    return McpAuthMiddleware(_configured_mcp().streamable_http_app())


def current_session_manager():
    """The FastMCP session manager, materialized if it doesn't exist yet.

    ``streamable_http_app()`` is what creates the manager lazily; the engine
    lifespan must call this rather than reading the property (which raises
    before the app has been built).
    """
    mcp = _configured_mcp()
    mcp.streamable_http_app()
    return mcp.session_manager


class LazyMcpMount:
    """ASGI mount that always serves the current session manager's app.

    The MCP SDK allows one ``run()`` per session-manager instance and tests
    (or in-process restarts) reset it; a mount holding a stale app would
    otherwise 500 after a reset.
    """

    def __init__(self):
        self._app = None
        self._manager = None

    async def __call__(self, scope, receive, send):
        from backend.mcp_server import mcp

        if self._app is None or self._manager is not mcp._session_manager:
            self._app = mcp_asgi_app()
            self._manager = mcp._session_manager
        await self._app(scope, receive, send)


def reset_session_manager() -> None:
    """Drop the cached session manager so a new ``run()`` is allowed.

    The MCP SDK's ``StreamableHTTPSessionManager`` can only be run once per
    instance. That is fine for a process serving one app, but tests (and
    in-process restarts) create several apps in one process — calling this
    between lifespans makes each one start with a fresh manager.
    """
    from backend.mcp_server import mcp

    mcp._session_manager = None  # noqa: SLF001 — SDK has no public reset


# ---------------------------------------------------------------------------
# Server card + health
# ---------------------------------------------------------------------------

def mcp_public_url() -> str:
    base = (os.environ.get("JAMBU_MCP_PUBLIC_URL") or "").strip().rstrip("/")
    return f"{base}/mcp/" if base else "http://127.0.0.1:8001/mcp/"


def server_card() -> dict:
    """MCP Server Card (the 2026 discovery format) — public, no secrets."""
    from backend import __version__
    from backend.mcp_server import mcp

    try:
        tools = sorted(t.name for t in mcp._tool_manager.list_tools())
    except Exception:
        tools = []
    profile = active_profile()
    return {
        "name": "io.github.pmaero-byte/jambubrowser",
        "title": "Jambubrowser",
        "description": DEFAULT_DESCRIPTION[:100],
        "version": __version__,
        "websiteUrl": "https://github.com/pmaero-byte/jambubrowser",
        "transports": [
            {"type": "streamable-http", "url": mcp_public_url()},
            {"type": "stdio", "command": "python -m backend.mcp_server"},
        ],
        "authentication": {
            "type": "bearer",
            "header": "Authorization: Bearer <api-key>",
            "alternate": "X-API-Key: <api-key>",
            "key_source": "POST /api-keys/create or JAMBU_MCP_TOKEN",
        },
        "profiles": {
            "active": profile,
            "available": ["full", "curated"],
            "curated_note": "curated excludes execute_tool (arbitrary execution)",
        },
        "tools": {"count": len(tools), "names": tools},
    }


async def health(_request):
    return JSONResponse({"status": "ok", "transport": "streamable-http"})


async def card(_request):
    return JSONResponse(server_card())


@asynccontextmanager
async def _lifespan(_app):
    """Run the MCP session manager for the standalone deployment.

    Starlette does not run lifespans of mounted sub-apps, so the root app
    owns it. (The engine mount does the same thing in its own lifespan.)
    """
    async with _configured_mcp().session_manager.run():
        yield


def build_app() -> Starlette:
    """Standalone root app: card + health + authed MCP at ``/mcp``."""
    return Starlette(
        routes=[
            Route("/health", health),
            Route("/.well-known/mcp-server-card.json", card),
            Route("/server-card.json", card),
            Mount("/mcp", app=mcp_asgi_app()),
        ],
        middleware=[Middleware(McpAuthMiddleware)],
        lifespan=_lifespan,
    )


def card_routes():
    """Routes to include in the engine (FastAPI router)."""
    from fastapi import APIRouter

    router = APIRouter(tags=["mcp-remote"])

    @router.get("/.well-known/mcp-server-card.json")
    async def _card() -> dict[str, Any]:
        return server_card()

    return router


# Note: the standalone `app` is built lazily on import so tests that patch
# env (token/profile) before importing get the right configuration.
app = build_app()
