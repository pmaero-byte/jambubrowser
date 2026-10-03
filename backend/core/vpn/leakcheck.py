"""Leak-check: can we *prove* the egress really goes through the tunnel/pool?

Every other layer in this package *assumes* traffic uses the configured
egress. This module measures it. It is deliberately diagnosis-only — it
never changes network state.

Three concrete questions are asked:

1. IP match: an IP-echo service reached through the manager's resolved
   proxy should NOT return the same address as a direct connection.
2. IPv6: does the tunnel path have an IPv6 exit, and does our policy
   expect one? An unexpected IPv6 route means the browser's IPv6+host AAAA
   records escape the VPN.
3. DNS: does the *system* resolution of a probe hostname match the answer
   the tunnel sees? Comparing both through DNS-over-HTTPS must agree —
   otherwise the browser's DNS bypasses the tunnel.

All probes are injectable (`get`, `resolve`, `doh`) so the checks are
testable without network. Any new probe kind should be added here rather
than inlined into a route.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("jambu.vpn.leakcheck")

DEFAULT_IP_URL = "https://api.ipify.org"
DEFAULT_IP6_URL = "https://api6.ipify.org"
DEFAULT_DOH_URL = "https://dns.google/resolve"

# Injected for tests / alternative providers.
GetFn = Callable[[str, Optional[str], float], Awaitable[Any]]
ResolveFn = Callable[[str], str]
DohFn = Callable[[str, Optional[str], float], Awaitable[str]]


async def _default_get(url: str, proxy_url: Optional[str], timeout: float) -> Optional[str]:
    from backend.core.socks import make_async_client

    client = make_async_client(timeout=timeout, proxy_url=proxy_url)
    try:
        resp = await client.get(url)
        return resp.text.strip() if resp.status_code == 200 else None
    except Exception:
        return None


def _default_resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return ""


async def _default_doh(host: str, proxy_url: Optional[str], timeout: float) -> str:
    """First A record from dns.google through `proxy_url` (or direct)."""
    url = f"{DEFAULT_DOH_URL}?name={host}&type=A"
    from backend.core.socks import make_async_client

    client = make_async_client(timeout=timeout, proxy_url=proxy_url)
    try:
        resp = await client.get(url, headers={"accept": "application/dns-json"})
        data = resp.json() if resp.status_code == 200 else {}
        answers = data.get("Answer") or []
        for answer in answers:
            if answer.get("type") == 1 and answer.get("data"):
                return answer["data"]
        return ""
    except Exception:
        return ""


async def run_leak_check(
    manager: Optional[Any],
    *,
    session_key: Optional[str] = None,
    timeout: float = 5.0,
    probe_host: Optional[str] = None,
    get: Optional[GetFn] = None,
    resolve: Optional[ResolveFn] = None,
    doh: Optional[DohFn] = None,
) -> dict[str, Any]:
    """Return a structured leak verdict for the configured egress."""
    get = get or _default_get
    resolve = resolve or _default_resolve
    doh = doh or _default_doh

    proxy = manager.resolve_proxy(session_key) if manager is not None else None
    # If the manager is disabled, nothing leaks: there is no tunnel to leak.
    tunnel_active = bool(proxy or (manager is not None and getattr(manager, "config", None) and manager.config.enabled))

    direct_ip, tunnel_ip, ipv6_direct, ipv6_tunnel = await asyncio.gather(
        get(DEFAULT_IP_URL, None, timeout),
        get(DEFAULT_IP_URL, proxy, timeout),
        get(DEFAULT_IP6_URL, None, timeout),
        get(DEFAULT_IP6_URL, proxy, timeout),
    )

    leaks: list[str] = []
    notes: list[str] = []

    if tunnel_active:
        if direct_ip and tunnel_ip and direct_ip == tunnel_ip:
            leaks.append("egress IP matches the direct connection IP — tunnel not carrying traffic")
        if tunnel_ip is None:
            notes.append("tunnel egress IP check unreachable; cannot confirm tunnel is carrying traffic")
        if ipv6_tunnel is not None:
            leaks.append("IPv6 route exists through the tunnel — browser AAAA lookups can escape IPv4 policy")
        if ipv6_direct is not None:
            notes.append("host has a direct IPv6 route; IPv6 traffic not via tunnel must be blocked locally")

        probe = probe_host or "cloudflare.com"
        system_ip, tunnel_dns_ip = await asyncio.gather(
            asyncio.to_thread(resolve, probe),
            doh(probe, proxy, timeout),
        )
        if system_ip and tunnel_dns_ip and system_ip != tunnel_dns_ip:
            notes.append(
                f"system DNS for {probe} ({system_ip}) differs from tunnel DNS ({tunnel_dns_ip}) — check resolvers"
            )
        elif system_ip and tunnel_dns_ip:
            notes.append(f"system DNS for {probe} matches the tunnel's resolver ({system_ip})")
        dns_verdict = (
            "matched" if system_ip and tunnel_dns_ip and system_ip == tunnel_dns_ip
            else ("diverged" if system_ip and tunnel_dns_ip else "inconclusive")
        )
    else:
        leaks = []
        notes = ["VPN is disabled; all traffic is direct"]
        system_ip = tunnel_dns_ip = ""
        dns_verdict = "not_applicable"

    return {
        "at": time.time(),
        "tunnel_active": tunnel_active,
        "direct_ip": direct_ip,
        "tunnel_ip": tunnel_ip if tunnel_active else None,
        "ipv6": {"direct": ipv6_direct, "tunnel": ipv6_tunnel if tunnel_active else None},
        "dns": {
            "probe_host": (probe_host or "cloudflare.com") if tunnel_active else "",
            "system_answer": system_ip or "",
            "tunnel_answer": tunnel_dns_ip or "",
            "verdict": dns_verdict,
        },
        "leaks": leaks,
        "notes": notes,
        "verdict": "leaking" if leaks else ("ok" if tunnel_active else "disabled"),
    }
