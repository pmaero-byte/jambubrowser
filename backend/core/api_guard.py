"""
API-key guard for the public web-data surface
=============================================

The engine binds to ``127.0.0.1`` by default, so local access has always been
"protected" by nothing but the bind address. That holds until someone binds
``0.0.0.0`` — a container, a VPS, a tunnel — at which point every endpoint is on
the network, including the crawling endpoints that fetch arbitrary URLs on your
behalf and the extraction endpoint that spends your model budget.

Policy, from ``JAMBU_WEBDATA_AUTH``:

* ``auto`` (default) — a key is required from **non-loopback** peers only, so
  local development stays frictionless while an exposed port is protected.
* ``required`` — always require a key. Use this behind a proxy or tunnel, where
  every request arrives looking local to the engine.
* ``off`` — never require a key. Explicit opt-out, for tests and throwaway boxes.

Keys are the existing ``jambu_…`` API keys (created via ``POST /api-keys/create``),
sent as ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import HTTPException, Request

from backend.core.ingress import Origin, classify_origin

log = logging.getLogger("jambu.api_guard")

_VALID_MODES = ("auto", "required", "off")


def auth_mode() -> str:
    raw = (os.environ.get("JAMBU_WEBDATA_AUTH") or "auto").strip().lower()
    return raw if raw in _VALID_MODES else "auto"


def extract_key(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.headers.get("x-api-key") or None


def peer_is_local(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = client.host if client else None
    return classify_origin(host) is Origin.LOCAL


def _validate(key: str):
    from backend.core.api_keys import validate_api_key

    return validate_api_key(key)


async def require_api_key(request: Request):
    """FastAPI dependency. Returns the validated key metadata, or None when the
    request is allowed without one."""
    mode = auth_mode()
    if mode == "off":
        return None

    local = peer_is_local(request)
    if mode == "auto" and local:
        return None

    key = extract_key(request)
    if not key:
        raise HTTPException(
            status_code=401,
            detail=(
                "API key required. Send 'Authorization: Bearer <jambu_...>' or "
                "'X-API-Key: <key>'. Create one with POST /api-keys/create. "
                "(Local requests are exempt while JAMBU_WEBDATA_AUTH=auto.)"
            ),
            headers={"WWW-Authenticate": 'Bearer realm="jambubrowser-webdata"'},
        )

    meta = _validate(key)
    if meta is None:
        log.warning("rejected web-data request with an invalid API key")
        raise HTTPException(
            status_code=401,
            detail="Invalid or inactive API key.",
            headers={"WWW-Authenticate": 'Bearer realm="jambubrowser-webdata"'},
        )
    return meta
