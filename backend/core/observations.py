"""
Observation quarantine — turning tool output into privileged-safe data
=====================================================================

A tool that reads the web returns prose. That prose is the one place untrusted
content reaches the deciding model, so it is never passed through as text:
it goes through :mod:`backend.core.quarantine` and comes back as typed fields.

This module is the adapter between the two — it knows which tools touch the web,
pulls the prose out of their result shapes, and renders the quarantined result
for the privileged agent.

Fail-closed by design: if the extraction produces no usable data, the
observation is replaced by an explicit note saying so. It is **never** replaced
by the original prose — falling back to the raw text would defeat the boundary
and would do so precisely when the model is behaving unexpectedly.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from backend.core.quarantine import QuarantineChat, quarantine_extract, render_for_privileged
from backend.core.schemas import schema_for_tool

log = logging.getLogger("jambu.observations")


# Tools whose output came from the web and must therefore be quarantined.
UNTRUSTED_TOOLS = frozenset({
    "web_search", "search", "scrape_url", "scrape", "research",
    "browse", "browser_task", "browser_session_run", "browser_test_flow",
    "act", "browser_act",
})

# Keys whose values are prose a page controls.
_PROSE_KEYS = ("markdown", "content", "text", "body", "snippet", "summary")

# Keys never worth sending to the extractor (huge, or already structural).
_SKIP_KEYS = ("html", "links", "headers", "screenshot", "images", "base64")


def quarantine_enabled() -> bool:
    """Whether tool observations are routed through the boundary.

    Default on. ``JAMBU_QUARANTINE_OBSERVATIONS=0`` disables it for
    debugging only — the resulting text is fenced-and-labelled, not quarantined,
    so the privileged model sees raw page prose again.
    """
    raw = os.environ.get("JAMBU_QUARANTINE_OBSERVATIONS", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def is_untrusted_tool(tool_name: str) -> bool:
    return (tool_name or "").strip() in UNTRUSTED_TOOLS


def prose_from(data: Any) -> str:
    """Pull the page-controlled prose out of a tool result.

    Handles the shapes the built-in tools actually return (a scraped document, a
    search-result list, a bare string) without dragging raw HTML along.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "\n".join(prose_from(item) for item in data)
    if not isinstance(data, dict):
        return str(data)

    parts: List[str] = []
    for key in _PROSE_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    if parts:
        return "\n\n".join(parts)

    # Search results: titles and snippets are page-controlled.
    rows = data.get("results")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                title = row.get("title") or row.get("name")
                snippet = row.get("snippet") or row.get("content") or row.get("description")
                if title:
                    parts.append(str(title))
                if snippet:
                    parts.append(str(snippet))
        if parts:
            return "\n".join(parts)

    # Last resort: scalar values only. A dict would drag nested structure
    # (and any HTML inside it) into the extractor.
    scalars = [
        str(v) for k, v in data.items()
        if k not in _SKIP_KEYS and isinstance(v, (str, int, float))
    ]
    return "\n".join(scalars)


async def quarantine_observation(
    tool_name: str,
    data: Any,
    chat: QuarantineChat,
    *,
    max_chars: int = 6000,
) -> str:
    """Return privileged-safe text for one tool observation.

    Trusted/internal tools pass through untouched — their output never came from
    a page. Web tools are extracted into typed fields.
    """
    if not is_untrusted_tool(tool_name):
        if isinstance(data, dict):
            return json.dumps(data)[:1500]
        return str(data)[:1500]

    text = prose_from(data)
    if not text.strip():
        return f"[{tool_name} returned no readable content]"

    schema = schema_for_tool(tool_name)
    result = await quarantine_extract(text, schema, chat, max_chars=max_chars)

    if result.injection_markers:
        log.warning(
            "quarantined %s output contained injection markers: %s",
            tool_name, ", ".join(result.injection_markers),
        )
    if not result.ok:
        # Fail closed: the prose is dropped, never shown. The agent is told the
        # observation is unavailable rather than being handed the raw text.
        log.info("quarantine produced no data for %s: %s", tool_name, result.problems)
        return f"[{tool_name}: content quarantined, no usable fields extracted]"

    return render_for_privileged(result, source=tool_name)


async def quarantine_observations(
    observations: Iterable[tuple],
    chat: QuarantineChat,
) -> List[str]:
    """Quarantine a sequence of ``(tool_name, data)`` pairs."""
    out: List[str] = []
    for tool_name, data in observations:
        out.append(await quarantine_observation(tool_name, data, chat))
    return out


# --------------------------------------------------------------------------
# Default chat adapter (the LLM registry)
# --------------------------------------------------------------------------

async def registry_chat(messages: List[Dict[str, str]]) -> str:
    """Run a quarantined extraction on the configured default provider.

    Deliberately a *separate* call from the privileged agent's own model calls:
    it is a tool-less completion whose only job is to fill a schema.
    """
    from backend.llm import ChatMessage, Role, get_registry

    role_map = {"system": Role.SYSTEM, "user": Role.USER, "assistant": Role.ASSISTANT}
    convo = [
        ChatMessage(role=role_map.get(m.get("role", "user"), Role.USER), content=m.get("content", ""))
        for m in messages
    ]
    resp = await get_registry().chat(convo, temperature=0.0)
    return resp.content or ""
