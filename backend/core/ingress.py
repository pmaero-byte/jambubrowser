"""
Single-way local ingress
========================

Exactly one channel may author an **instruction**: the local operator.
Everything else — the web, a remote MCP client, another agent speaking A2A —
may describe work but can never define it.

This module is the one place that decision is made, kept deliberately small and
dependency-free so it can be enforced from HTTP routes, MCP and A2A without
importing the agent or the LLM layer.

The rule, stated as code:

    may_instruct == (source is a local surface) AND (the peer is loopback)

Authentication is *not* sufficient on its own. A correctly-authenticated remote
caller is still remote, so it still gets data-only. That is the whole point:
"inputs can be given only via local" is a property of the channel, not of the
credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Source(str, Enum):
    """Which surface a request arrived on."""

    UI = "ui"        # the local front-end
    CLI = "cli"      # `jambu …` on this machine
    A2A = "a2a"      # another agent hiring us (remote by design)
    MCP = "mcp"      # remote MCP client
    HTTP = "http"    # direct API call


class Origin(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


# Surfaces that may never author an instruction, by architecture — regardless of
# authentication, and regardless of where they connect from. A2A and MCP exist
# so other systems can *use* the engine; letting them define work would mean
# there is more than one instruction source.
DATA_ONLY_SOURCES = frozenset({Source.A2A, Source.MCP})

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


class InstructionRefused(Exception):
    """A request that is not entitled to author an instruction tried to."""


@dataclass(frozen=True)
class Ingress:
    """The decided provenance of one inbound request."""

    source: Source
    origin: Origin
    authenticated: bool = False
    detail: str = ""

    @property
    def may_instruct(self) -> bool:
        """True only for a local surface reached over loopback."""
        return self.source not in DATA_ONLY_SOURCES and self.origin is Origin.LOCAL

    @property
    def is_data_only(self) -> bool:
        return not self.may_instruct


def classify_origin(client_host: Optional[str]) -> Origin:
    """Map a peer address to LOCAL/REMOTE.

    An unknown peer is treated as REMOTE: failing closed means a request whose
    origin we cannot establish is never allowed to instruct.
    """
    if not client_host:
        return Origin.REMOTE
    host = client_host.strip().lower()
    if host.startswith("["):  # bracketed IPv6
        host = host.strip("[]")
    # ASGI sometimes reports "127.0.0.1:54321"; TestClient reports "testclient".
    if ":" in host and host.count(":") == 1:
        host = host.split(":", 1)[0]
    if host in _LOOPBACK_HOSTS:
        return Origin.LOCAL
    if host.startswith("127."):
        return Origin.LOCAL
    return Origin.REMOTE


def authorize_ingress(
    source: Source,
    *,
    client_host: Optional[str] = None,
    authenticated: bool = False,
) -> Ingress:
    """Decide what an inbound request is allowed to do. Never raises."""
    origin = classify_origin(client_host)
    detail = ""
    if source in DATA_ONLY_SOURCES:
        detail = f"{source.value} is a data-only surface by architecture"
    elif origin is Origin.REMOTE:
        detail = "peer is not loopback"
    return Ingress(source=source, origin=origin, authenticated=authenticated, detail=detail)


def assert_may_instruct(ingress: Ingress, what: str = "an instruction") -> None:
    """Raise ``InstructionRefused`` unless this ingress may author work."""
    if not ingress.may_instruct:
        who = ingress.source.value
        raise InstructionRefused(
            f"{who} may not author {what}: {ingress.detail or 'not a local operator channel'}. "
            f"Only the local operator can define work; other surfaces may request data."
        )
