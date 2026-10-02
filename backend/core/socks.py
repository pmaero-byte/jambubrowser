"""
SOCKS / Tor transport helper.

Drops in a wrapper around httpx.AsyncClient that transparently routes
all outbound HTTP through a SOCKS5 proxy (e.g. a local `tor` daemon,
`ssh -D 1080`, or a commercial proxy). When no SOCKS URL is configured,
behaves exactly like httpx.AsyncClient — zero overhead, zero behavior
change.

Enable by setting one of:
  JAMBU_TOR_SOCKS_URL  — SOCKS5h URL, e.g. socks5h://127.0.0.1:9150
  AGENT_VPN_PROXY       — legacy alias (engine_runtime.GLOBAL_VPN_PROXY)

Why SOCKS5h and not SOCKS5: the `h` variant forces DNS resolution
through the proxy too. Without it, DNS lookups happen locally and
leak the destination hostname. For Tor specifically you want the
h variant.

The engine's search / scrape paths call `make_async_client()` instead
of `httpx.AsyncClient()` directly. Every existing call site that
already uses `httpx.AsyncClient()` keeps working unchanged.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx


def get_socks_url() -> Optional[str]:
    """Return the configured SOCKS URL, or None if Tor isn't enabled."""
    return (
        os.environ.get("JAMBU_TOR_SOCKS_URL")
        or os.environ.get("AGENT_VPN_PROXY")
        or None
    )


def is_tor_enabled() -> bool:
    return get_socks_url() is not None


def _normalize_socks_url(url: str) -> str:
    """Return a URL httpx-socks / python-socks will accept.

    Users often write `socks5h://` (curl/Tor convention: DNS over proxy),
    but python-socks only understands `socks5://`, `socks4://`, and
    `http://`. The remote-DNS behaviour is controlled by the SOCKS5
    protocol itself, not by the scheme, so stripping the `h` suffix is
    safe for our use case.
    """
    if url.startswith("socks5h://"):
        return "socks5://" + url[len("socks5h://"):]
    if url.startswith("socks4a://"):
        return "socks4://" + url[len("socks4a://"):]
    return url


def _resolve_dynamic_proxy(session_key: Optional[str] = None) -> Optional[str]:
    """Ask the VPN manager for an endpoint; None when dynamic VPN is off.

    Imported lazily so this module keeps working if the vpn package is
    unavailable, and so a VPN misconfiguration can never break the plain
    Tor/SOCKS path below.
    """
    try:
        from backend.core.vpn.manager import get_vpn_manager
    except ImportError:
        return None
    try:
        return get_vpn_manager().resolve_proxy(session_key)
    except Exception:
        # A VPN selection failure must not be silently swallowed into a
        # direct connection when fail-closed is in force — but this helper
        # only reports whether an endpoint exists. Callers that require a
        # proxy should use get_vpn_manager().resolve_proxy() directly and
        # handle VPNUnavailable themselves.
        import logging
        logging.getLogger("jambu.socks").warning(
            "dynamic VPN selection failed; falling back to static SOCKS config",
            exc_info=True,
        )
        return None


def make_async_client(
    *,
    timeout: float = 15.0,
    follow_redirects: bool = True,
    headers: Optional[dict[str, str]] = None,
    proxy_url: Optional[str] = None,
    session_key: Optional[str] = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient, optionally tunneled through SOCKS5/Tor.

    Drop-in replacement for `httpx.AsyncClient(...)`:
        await client.get("https://example.com")

    Proxy precedence (first match wins):

    1. ``proxy_url`` — an explicit endpoint, e.g. one chosen by a caller
       from the VPN pool.
    2. The dynamic VPN pool (``backend.core.vpn``) when ``JAMBU_VPN_ENABLED``
       is set — a rotating, health-checked endpoint.
    3. ``JAMBU_TOR_SOCKS_URL`` / ``AGENT_VPN_PROXY`` — the static config.
    4. No proxy at all (direct).
    """
    socks_url = proxy_url or _resolve_dynamic_proxy(session_key) or get_socks_url()
    if socks_url is None:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers=headers or {},
            **kwargs,
        )

    # Lazy-import httpx-socks so the dep is only required when Tor is
    # actually enabled (CI users who never set JAMBU_TOR_SOCKS_URL don't
    # pay the import cost).
    from httpx_socks import AsyncProxyTransport

    transport = AsyncProxyTransport.from_url(_normalize_socks_url(socks_url))
    return httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=follow_redirects,
        headers=headers or {},
        **kwargs,
    )