"""
Dynamic VPN configuration.

One surface describing the whole egress path, loaded from the environment so
the engine, the desktop shell, and CI all configure VPN the same way.

Layering (see ``manager.py``): a **tunnel** (WireGuard/OpenVPN) forms the base
egress; a **pool** of proxy endpoints sits on top for per-request selection.
Either layer may be disabled independently.

Design rules, matching the rest of the product:

* **Zero behaviour change when unconfigured.** With no VPN env set, every
  helper here returns "direct" and callers fall through to their existing path.
* **Fail closed by default.** If VPN is *required* and the path cannot be
  established, callers get an explicit error instead of silently leaking the
  real IP. Opt out explicitly with ``JAMBU_VPN_FAIL_OPEN=1``.
* **No secrets in the config surface.** Endpoints may carry credentials, so
  :meth:`VPNConfig.redacted` is what gets logged or returned over HTTP.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse


class RotationPolicy(str, Enum):
    """How the pool picks an endpoint for a new unit of work."""

    FAILOVER = "failover"        # stay on the current endpoint until it fails
    ROUND_ROBIN = "round_robin"  # rotate on every selection
    RANDOM = "random"            # uniform random pick among healthy
    LEAST_LATENCY = "least_latency"  # lowest observed latency wins


class TunnelKind(str, Enum):
    WIREGUARD = "wireguard"
    OPENVPN = "openvpn"
    NONE = "none"


_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4")


@dataclass(frozen=True)
class VPNConfig:
    """Resolved VPN configuration.

    Immutable so a running session cannot have its egress changed underneath
    it; use :meth:`evolve` to derive a modified copy.
    """

    # Master switch. When False the whole subsystem is inert.
    enabled: bool = False
    # When True, selection failures raise instead of falling back to direct.
    fail_closed: bool = True

    # -- pool layer --------------------------------------------------------
    pool: tuple[str, ...] = ()
    rotation: RotationPolicy = RotationPolicy.FAILOVER
    # Only consider endpoints tagged with one of these regions (empty = any).
    allowed_regions: tuple[str, ...] = ()
    # Seconds between background health sweeps.
    health_interval: float = 30.0
    # Per-probe timeout in seconds.
    health_timeout: float = 5.0
    # Consecutive failures before an endpoint is considered dead.
    failure_threshold: int = 3
    # Target the health probe fetches. Empty means latency-only, no request.
    health_probe_url: str = ""

    # -- tunnel layer ------------------------------------------------------
    tunnel_kind: TunnelKind = TunnelKind.NONE
    tunnel_interface: str = ""
    tunnel_endpoint: str = ""
    tunnel_config_path: str = ""
    tunnel_dns: tuple[str, ...] = ()

    # -- misc --------------------------------------------------------------
    # Sticky window: how long one session keeps its endpoint.
    sticky_ttl: float = 300.0
    # Where pool health is persisted so a restart does not wipe what the
    # mesh learned about a dead endpoint. Empty means in-memory only.
    state_file: str = ""
    # Restore entries at most this old; older files are ignored so a week-old
    # quarantine never pins a recovered endpoint for new sessions.
    state_max_age: float = 86400.0

    @classmethod
    def from_env(cls, env: Optional[Any] = None) -> "VPNConfig":
        """Build a config from an env mapping (defaults to ``os.environ``).

        Recognised variables:

        ``JAMBU_VPN_ENABLED``          master switch (default off)
        ``JAMBU_VPN_FAIL_OPEN``        set to 1 to allow direct fallback
        ``JAMBU_VPN_POOL``             comma-separated proxy URLs
        ``JAMBU_VPN_ROTATION``         failover|round_robin|random|least_latency
        ``JAMBU_VPN_REGIONS``          comma-separated allowed regions
        ``JAMBU_VPN_HEALTH_INTERVAL``  seconds between health sweeps (30)
        ``JAMBU_VPN_HEALTH_TIMEOUT``   per-probe timeout in seconds (5)
        ``JAMBU_VPN_FAILURE_THRESHOLD`` failures before an endpoint is dead (3)
        ``JAMBU_VPN_HEALTH_PROBE_URL`` URL the probe fetches (optional)
        ``JAMBU_VPN_TUNNEL``           wireguard|openvpn
        ``JAMBU_VPN_TUNNEL_INTERFACE`` interface name to manage
        ``JAMBU_VPN_TUNNEL_ENDPOINT``  peer endpoint host:port
        ``JAMBU_VPN_TUNNEL_CONFIG``    path to a .conf
        ``JAMBU_VPN_TUNNEL_DNS``       comma-separated DNS servers
        ``JAMBU_VPN_STICKY_TTL``       seconds a session keeps its endpoint (300)
        ``JAMBU_VPN_STATE_FILE``       JSON path for pool-health persistence (off)
        ``JAMBU_VPN_STATE_MAX_AGE``    oldest state to restore, seconds (86400)
        """
        src = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            return str(src.get(name) or default).strip()

        def get_list(name: str) -> tuple[str, ...]:
            raw = src.get(name) or ""
            return tuple(part.strip() for part in raw.split(",") if part.strip())

        tunnel_raw = get("JAMBU_VPN_TUNNEL").lower()
        try:
            tunnel_kind = TunnelKind(tunnel_raw) if tunnel_raw else TunnelKind.NONE
        except ValueError:
            tunnel_kind = TunnelKind.NONE

        rotation_raw = get("JAMBU_VPN_ROTATION", RotationPolicy.FAILOVER.value).lower()
        try:
            rotation = RotationPolicy(rotation_raw)
        except ValueError:
            rotation = RotationPolicy.FAILOVER

        return cls(
            enabled=_env_bool_from(src, "JAMBU_VPN_ENABLED"),
            fail_closed=not _env_bool_from(src, "JAMBU_VPN_FAIL_OPEN"),
            pool=get_list("JAMBU_VPN_POOL"),
            rotation=rotation,
            allowed_regions=get_list("JAMBU_VPN_REGIONS"),
            health_interval=_env_float_from(
                src, "JAMBU_VPN_HEALTH_INTERVAL", 30.0, minimum=1.0
            ),
            health_timeout=_env_float_from(
                src, "JAMBU_VPN_HEALTH_TIMEOUT", 5.0, minimum=0.1
            ),
            failure_threshold=_env_int_from(
                src, "JAMBU_VPN_FAILURE_THRESHOLD", 3
            ),
            health_probe_url=get("JAMBU_VPN_HEALTH_PROBE_URL"),
            tunnel_kind=tunnel_kind,
            tunnel_interface=get("JAMBU_VPN_TUNNEL_INTERFACE"),
            tunnel_endpoint=get("JAMBU_VPN_TUNNEL_ENDPOINT"),
            tunnel_config_path=get("JAMBU_VPN_TUNNEL_CONFIG"),
            tunnel_dns=get_list("JAMBU_VPN_TUNNEL_DNS"),
            sticky_ttl=_env_float_from(src, "JAMBU_VPN_STICKY_TTL", 300.0),
            state_file=get("JAMBU_VPN_STATE_FILE"),
            state_max_age=_env_float_from(
                src, "JAMBU_VPN_STATE_MAX_AGE", 86400.0, minimum=60.0
            ),
        )

    # -- derived state -----------------------------------------------------

    @property
    def pool_enabled(self) -> bool:
        return self.enabled and bool(self.pool)

    @property
    def tunnel_enabled(self) -> bool:
        return self.enabled and self.tunnel_kind is not TunnelKind.NONE

    def evolve(self, **changes: Any) -> "VPNConfig":
        """Return a copy with ``changes`` applied (the config is frozen)."""
        return replace(self, **changes)

    def validates(self) -> list[str]:
        """Return human-readable problems; an empty list means valid."""
        problems: list[str] = []
        if self.enabled and not self.pool and not self.tunnel_enabled:
            problems.append(
                "VPN is enabled but neither a pool nor a tunnel is configured"
            )
        if self.tunnel_enabled:
            if not self.tunnel_interface:
                problems.append("tunnel requires JAMBU_VPN_TUNNEL_INTERFACE")
            if not self.tunnel_endpoint and not self.tunnel_config_path:
                problems.append(
                    "tunnel requires JAMBU_VPN_TUNNEL_ENDPOINT or "
                    "JAMBU_VPN_TUNNEL_CONFIG"
                )
        for url in self.pool:
            try:
                parsed = urlparse(url)
            except ValueError:
                problems.append(f"malformed proxy URL: {redact_proxy_url(url)}")
                continue
            if parsed.scheme not in _PROXY_SCHEMES:
                problems.append(
                    f"unsupported proxy scheme '{parsed.scheme}' in "
                    f"{redact_proxy_url(url)}"
                )
            if not parsed.hostname:
                problems.append(f"proxy entry has no host: {redact_proxy_url(url)}")
        if self.health_probe_url:
            try:
                scheme = urlparse(self.health_probe_url).scheme
            except ValueError:
                scheme = ""
            if not scheme.startswith("http"):
                problems.append("health probe URL must be http(s)")
        return problems

    def is_valid(self) -> bool:
        return not self.validates()

    @property
    def dry_run(self) -> bool:
        """Simulate tunnel bring-up without root or a vendor binary.

        Read from ``JAMBU_VPN_DRY_RUN``. This is what lets CI (and a curious
        developer) exercise the tunnel code path end to end without touching
        the host network stack.
        """
        return _env_bool_from(os.environ, "JAMBU_VPN_DRY_RUN")

    def redacted(self) -> dict[str, Any]:
        """A JSON-safe view with credentials removed — safe to log/return."""
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "fail_closed": self.fail_closed,
            "pool_enabled": self.pool_enabled,
            "pool": [redact_proxy_url(u) for u in self.pool],
            "rotation": self.rotation.value,
            "allowed_regions": list(self.allowed_regions),
            "health_interval": self.health_interval,
            "health_timeout": self.health_timeout,
            "failure_threshold": self.failure_threshold,
            "health_probe_url": self.health_probe_url,
            "tunnel_kind": self.tunnel_kind.value,
            "tunnel_interface": self.tunnel_interface,
            "tunnel_endpoint": self.tunnel_endpoint,
            "tunnel_config_path": self.tunnel_config_path,
            "tunnel_dns": list(self.tunnel_dns),
            "sticky_ttl": self.sticky_ttl,
            "state_file": self.state_file,
            "state_max_age": self.state_max_age,
        }


def load_config() -> VPNConfig:
    """Read a :class:`VPNConfig` from the process environment."""
    return VPNConfig.from_env(os.environ)


def redact_proxy_url(url: str) -> str:
    """Strip any embedded credentials so a URL is safe to log or return.

    ``socks5h://user:pass@host:1080`` -> ``socks5h://***:***@host:1080``.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.username and not parsed.password:
        return url
    host = parsed.hostname or ""
    try:
        if parsed.port:
            host = f"{host}:{parsed.port}"
    except ValueError:  # malformed port
        pass
    userinfo = "***:***@" if parsed.password else "***@"
    return urlunparse(
        (parsed.scheme, f"{userinfo}{host}", parsed.path, "", parsed.query, "")
    )


def _env_bool_from(src: Any, name: str, default: bool = False) -> bool:
    raw = src.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int_from(src: Any, name: str, default: int, *, minimum: int = 1) -> int:
    raw = src.get(name)
    if not raw:
        return default
    try:
        value = int(str(raw))
    except ValueError:
        return default
    return max(value, minimum)


def _env_float_from(
    src: Any, name: str, default: float, *, minimum: float = 0.0
) -> float:
    raw = src.get(name)
    if not raw:
        return default
    try:
        value = float(str(raw))
    except ValueError:
        return default
    return max(value, minimum)