"""VPN egress MCP tools.

Dynamic egress: tunnel status, proxy selection and probing.

Tools are plain async functions here; ``register(mcp)`` applies FastMCP's
decorator to each one, so a tool can be read, imported and unit-tested
without an MCP server in the loop.
"""
from __future__ import annotations

from backend.mcp_tools import _shared


async def vpn_status() -> str:
    """
    Show the dynamic VPN state: tunnel, pool size, and per-endpoint health.

    Credentials are redacted in every output. When VPN is disabled the pool
    is empty and resolution falls through to a direct connection.
    """
    result = await _shared.call_engine("GET", "/vpn/status", timeout=20.0)
    if "error" in result:
        return f"VPN status failed: {result['error']}"
    lines = ["# VPN Status\n"]
    lines.append(f"- Active: {'yes' if result.get('active') else 'no'} · fail-closed: {result.get('fail_closed')}")
    t = result.get("tunnel") or {}
    lines.append(f"- Tunnel: {t.get('kind', 'none')} / {t.get('state', 'unknown')} on {t.get('interface') or '—'}")
    pool = result.get("pool") or {}
    lines.append(f"- Rotation: {pool.get('rotation', 'failover')} · sticky sessions: {pool.get('sticky_sessions', 0)}")
    lines.append(f"- Pool: {pool.get('healthy', 0)}/{pool.get('size', 0)} endpoints healthy")
    for ep in pool.get("endpoints") or []:
        flag = "✅" if ep.get("available") else "⛔"
        latency = ep.get("latency_ms")
        lines.append(
            f"  - {flag} `{ep.get('url', '?')}` ({ep.get('region') or 'any region'}) "
            f"latency={'~' + str(latency) + 'ms' if latency is not None else '—'} "
            f"fails={ep.get('consecutive_failures', 0)}"
        )
    for problem in result.get("problems") or []:
        lines.append(f"- ⚠ {problem}")
    return "\n".join(lines)


async def vpn_select(session_key: str = "", exclude: str = "") -> str:
    """
    Resolve which egress endpoint a session would be pinned to.

    Does not open a connection — it answers "which proxy would this session
    use right now", which is what you want before launching a browser or CI
    job. Pass exclude as a comma-separated list of endpoints to skip.

    Args:
        session_key: Pin this key to one endpoint for the sticky TTL
        exclude: Comma-separated endpoint URLs/aliases to skip
    """
    payload = {"session_key": session_key or None, "exclude": [e for e in exclude.split(",") if e.strip()]}
    result = await _shared.call_engine("POST", "/vpn/select", payload, timeout=20.0)
    if "error" in result:
        return f"VPN select failed: {result['error']}"
    if result.get("direct"):
        return "Direct connection (no proxy endpoint resolved)."
    return f"Endpoint: `{result.get('redacted_proxy')}`"


async def vpn_probe() -> str:
    """
    Run one health sweep across the proxy pool right now.

    Endpoints that fail consecutive probes are quarantined for a bounded,
    self-healing window. Returns how many endpoints probed clean.
    """
    result = await _shared.call_engine("POST", "/vpn/probe", timeout=60.0)
    if "error" in result:
        return f"VPN probe failed: {result['error']}"
    probe = result.get("probe") or result
    if probe.get("probed") == 0:
        return f"No probe ran: {probe.get('reason', 'no health probe configured')}."
    return f"Probe complete: {probe.get('probed')} endpoint(s) checked; see vpn_status for per-endpoint health."



def register(mcp) -> None:
    """Register all 3 vpn tools with the server."""
    mcp.tool()(vpn_status)
    mcp.tool()(vpn_select)
    mcp.tool()(vpn_probe)
