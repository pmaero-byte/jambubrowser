"""The one place an MCP tool talks to the Jambubrowser engine.

Every tool reaches the engine through ``_shared.call_engine`` *by attribute*,
not by ``from ... import call_engine``. That is deliberate: it keeps a single
patch point for tests and for anyone embedding the server, the way
``mcp_server._call_engine`` used to be one. A from-import would bind the
function at import time and every test would have to patch each tool module
separately.

The error contract matters as much as the transport: a tool must never raise.
Callers get ``{"error": ...}`` describing what went wrong, because the consumer
of an MCP tool is a model, not a try/except block.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("jambu.mcp_tools")

# Engine URL is overridable via JAMBU_ENGINE_URL (useful for tests that spawn
# the engine on a free port). Default matches the conventional dev port.
ENGINE_URL = os.environ.get("JAMBU_ENGINE_URL", "http://localhost:8001")
DEFAULT_TIMEOUT = 60.0


async def call_engine(
    method: str,
    path: str,
    json_data: dict = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Call the engine API and return its JSON, or ``{"error": ...}``."""
    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                resp = await client.get(
                    f"{ENGINE_URL}{path}",
                    params=json_data,
                    timeout=timeout,
                )
            elif method == "POST":
                resp = await client.post(
                    f"{ENGINE_URL}{path}",
                    json=json_data or {},
                    timeout=timeout,
                )
            else:
                return {"error": f"Unsupported method: {method}"}

            if resp.status_code == 200:
                return resp.json()
            # Surface the engine's own error detail (e.g. DCM's missing-runtime
            # explanation from /dcm/infer) instead of a bare status code.
            detail = None
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = body.get("detail") or body.get("error")
            except Exception:
                # A non-JSON error body (an HTML 502 from a proxy, say) leaves
                # us with the status code alone — which the caller reports.
                log.debug("engine error body was not JSON", exc_info=True)
                detail = None
            if detail:
                return {"error": f"Engine HTTP {resp.status_code}: {detail}"}
            return {"error": f"Engine returned status {resp.status_code}"}
        except httpx.TimeoutException:
            return {"error": f"Request timed out after {timeout}s"}
        except httpx.ConnectError:
            return {"error": "Engine is not running. Start it with: python engine.py"}
        except Exception as e:
            return {"error": str(e)}
