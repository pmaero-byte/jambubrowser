"""
Dynamic VPN for Jambubrowser.

Two layers behind one façade:

* :mod:`backend.core.vpn.tunnel` — a system-level VPN (WireGuard / OpenVPN)
  that forms the base egress.
* :mod:`backend.core.vpn.pool` — a rotating, health-checked set of proxy
  endpoints that decides *which* egress a given request uses.

Typical use::

    from backend.core.vpn import get_vpn_manager

    proxy = get_vpn_manager().resolve_proxy(session_key="session-1")
    # proxy is None when VPN is not configured — callers pass it straight
    # through to httpx / Playwright.

Everything is inert unless ``JAMBU_VPN_ENABLED`` is set.
"""

from backend.core.vpn.config import (
    RotationPolicy,
    TunnelKind,
    VPNConfig,
    load_config,
    redact_proxy_url,
)
from backend.core.vpn.manager import (
    VPNManager,
    VPNUnavailable,
    get_vpn_manager,
    reset_vpn_manager,
    resolve_proxy,
)
from backend.core.vpn.pool import EndpointHealth, NoHealthyEndpoint, ProxyPool
from backend.core.vpn.tunnel import (
    NullTunnel,
    OpenVPNTunnel,
    TunnelError,
    TunnelManager,
    TunnelState,
    TunnelStatus,
    WireGuardTunnel,
    build_backend,
)

__all__ = [
    "RotationPolicy",
    "TunnelKind",
    "VPNConfig",
    "load_config",
    "redact_proxy_url",
    "VPNManager",
    "VPNUnavailable",
    "get_vpn_manager",
    "reset_vpn_manager",
    "resolve_proxy",
    "EndpointHealth",
    "NoHealthyEndpoint",
    "ProxyPool",
    "NullTunnel",
    "OpenVPNTunnel",
    "TunnelError",
    "TunnelManager",
    "TunnelState",
    "TunnelStatus",
    "WireGuardTunnel",
    "build_backend",
]