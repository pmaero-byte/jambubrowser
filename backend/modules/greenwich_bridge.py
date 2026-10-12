"""Greenwich Studio integration — the JambuBrowser side of the bridge.

One module owns the Memory Bridge contract, so research, scrape, audit and the
browser-session route cannot drift apart:

* ``ingest_memory`` posts one artifact (research answer, scrape, audit summary)
  and resolves the endpoint the way a real Greenwich deployment answers:
  1. ``/v1/memory/ingest`` — the documented contract (Greenwich spec §5).
  2. ``/v1/trajectory/record`` — what the deployed ``memory_bridge.py``
     actually exposes: a TITO model-call record with ``input_text`` /
     ``output_text`` / ``metadata`` that prefix-dedupes into the trajectory
     tree. A 404 on (1) means "this deployment is older/newer than the spec",
     not an error.
* ``memory_status`` reads ``GET /v1/project/memory`` so a UI can show whether
  the bridge is live before offering "save to memory".
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("jambu.greenwich")

# Greenwich Studio Memory Bridge (spec §5). Override for other deployments.
DEFAULT_MEMORY_URL = "http://127.0.0.1:8095"

# Endpoint resolution order. The first path that is not a 404 wins.
INGEST_PATHS = ("/v1/memory/ingest", "/v1/trajectory/record")

# Role recorded on the trajectory node. ``memory_bridge.AgentRole`` falls back
# to MAIN_AGENT_TASK for unknown values, but sending the valid default keeps
# the node correctly labelled in the tree.
TRAJECTORY_ROLE = "main_agent_task"


def memory_bridge_url() -> str:
    """Base URL of the Memory Bridge (``GREENWICH_MEMORY_URL``)."""
    return (os.environ.get("GREENWICH_MEMORY_URL", DEFAULT_MEMORY_URL)
            or DEFAULT_MEMORY_URL).rstrip("/")


def _spec_payload(*, title: str, clean_markdown: str, url: str,
                  citations: list, metadata: dict) -> dict:
    """Greenwich spec §5 ingest body."""
    return {
        "source": "jambubrowser",
        "url": url or "",
        "title": title or "",
        "clean_markdown": clean_markdown or "",
        "citations": citations or [],
        "metadata": metadata or {},
    }


def _trajectory_payload(*, title: str, clean_markdown: str, url: str,
                        citations: list, metadata: dict) -> dict:
    """Body for the deployed ``/v1/trajectory/record`` (TITO record).

    The bridge stores one request/response pair per node, so the artifact's
    title/URL are the *request* text, the cleaned markdown is the *response*,
    and everything else rides along in metadata where the trajectory tree
    already looks for audit context.
    """
    meta = dict(metadata or {})
    meta.update({"source": "jambubrowser", "url": url or "",
                 "title": title or "", "citations": citations or []})
    return {
        "input_text": title or url or "jambubrowser artifact",
        "output_text": clean_markdown or "",
        "role": TRAJECTORY_ROLE,
        "metadata": meta,
    }


async def ingest_memory(*, title: str = "", clean_markdown: str = "",
                        url: str = "", citations: Optional[list] = None,
                        metadata: Optional[dict] = None,
                        timeout: float = 15.0) -> dict:
    """Fire one artifact at the Memory Bridge; never raises.

    Returns ``{"ok": bool, "endpoint": "/v1/...", "status": int}`` on success,
    or ``{"ok": False, "error": ...}`` when the bridge is unreachable. Callers
    treat this as fire-and-forget: a memory outage must not fail a research
    run or an audit.
    """
    base = memory_bridge_url()
    citations = citations or []
    metadata = metadata or {}
    spec_body = _spec_payload(title=title, clean_markdown=clean_markdown,
                              url=url, citations=citations, metadata=metadata)
    traj_body = _trajectory_payload(title=title, clean_markdown=clean_markdown,
                                    url=url, citations=citations, metadata=metadata)
    last_error = ""
    async with httpx.AsyncClient() as client:
        for path in INGEST_PATHS:
            body = spec_body if path == INGEST_PATHS[0] else traj_body
            try:
                r = await client.post(f"{base}{path}", json=body, timeout=timeout)
            except Exception as exc:  # unreachable/DNS/timeout — try the next
                last_error = f"{path}: {exc!r}"[:200]
                continue
            if r.status_code == 404:
                last_error = f"{path}: 404"
                continue
            if r.status_code < 300:
                return {"ok": True, "endpoint": path, "status": r.status_code}
            last_error = f"{path}: {r.status_code} {r.text[:160]}"
    log.debug("greenwich ingest failed: %s", last_error)
    return {"ok": False, "error": last_error or "unknown"}


async def memory_status(timeout: float = 5.0) -> dict:
    """``GET /v1/project/memory`` — bridge liveness + telemetry, never raises."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{memory_bridge_url()}/v1/project/memory",
                                 timeout=timeout)
            if r.status_code < 300:
                data = r.json()
                return {"ok": True, "bridge": data}
            return {"ok": False, "error": f"status {r.status_code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def payload_preview(body: dict) -> str:
    """Compact one-line rendering for logs/tests."""
    return json.dumps({k: str(v)[:60] for k, v in (body or {}).items()})
