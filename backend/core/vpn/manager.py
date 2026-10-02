"""
VPN manager — the single façade over both layers.

    request ──► VPNManager.resolve_proxy()
                  │
                  ├─ tunnel layer  (WireGuard/OpenVPN)  ── base egress
                  └─ pool layer    (proxy endpoints)    ── dynamic selection

Callers do not need to know which layers are active. With nothing configured
:meth:`resolve_proxy` returns ``None`` and every existing code path keeps
behaving exactly as before — that property is what lets this ship as a default
capability without breaking single-user setups.

Fail-closed by default: if VPN is enabled but no usable path exists,
:meth:`resolve_proxy` raises :class:`VPNUnavailable` rather than silently
connecting from the real IP.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from backend.core.vpn.config import VPNConfig, load_config, redact_proxy_url
from backend.core.vpn.pool import NoHealthyEndpoint, ProxyPool
from backend.core.vpn.tunnel import TunnelManager, TunnelState, TunnelStatus

log = logging.getLogger("jambu.vpn")


class VPNUnavailable(RuntimeError):
    """VPN is required but no usable egress path could be established."""


class VPNManager:
    """Process-wide façade over the tunnel and pool layers."""

    def __init__(
        self,
        config: Optional[VPNConfig] = None,
        *,
        dry_run: Optional[bool] = None,
        pool: Optional[ProxyPool] = None,
        tunnel: Optional[TunnelManager] = None,
    ):
        self._config = config if config is not None else load_config()
        # Default dry-run to the environment so JAMBU_VPN_DRY_RUN=1 works
        # without every caller having to thread the flag through.
        if dry_run is None:
            dry_run = self._config.dry_run
        self._dry_run = bool(dry_run)
        self._tunnel = tunnel or TunnelManager(self._config, dry_run=self._dry_run)
        self._pool = pool or ProxyPool(self._config)
        self._lock = threading.Lock()
        self._started = False

    # -- introspection -----------------------------------------------------

    @property
    def config(self) -> VPNConfig:
        return self._config

    @property
    def pool(self) -> ProxyPool:
        return self._pool

    @property
    def tunnel(self) -> TunnelManager:
        return self._tunnel

    @property
    def dry_run(self) -> bool:
        """True when tunnel bring-up is simulated rather than executed."""
        return self._dry_run

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def active(self) -> bool:
        """True when at least one layer is doing something."""
        return self._config.pool_enabled or self._config.tunnel_enabled

    def problems(self) -> list[str]:
        return self._config.validates()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> dict[str, Any]:
        """Bring up the tunnel (if any) and start the health sweeper."""
        with self._lock:
            if self._started:
                return await self.status()
            self._started = True

        tunnel_status: Optional[TunnelStatus] = None
        if self._config.tunnel_enabled:
            tunnel_status = await self._tunnel.start()
            if tunnel_status.state != TunnelState.UP.value:
                # Fail closed: a tunnel we were told to use but could not
                # raise must not degrade into a direct connection.
                if self._config.fail_closed and not self._config.pool_enabled:
                    self._started = False
                    raise VPNUnavailable(
                        f"tunnel {tunnel_status.kind} unavailable: "
                        f"{tunnel_status.last_error or tunnel_status.state}"
                    )
                log.warning(
                    "vpn tunnel %s unavailable (%s); continuing with pool",
                    tunnel_status.kind,
                    tunnel_status.last_error or tunnel_status.state,
                )

        if self._config.pool_enabled and self._config.health_probe_url:
            self._pool.start_sweeper()

        return await self.status()

    async def stop(self) -> dict[str, Any]:
        with self._lock:
            self._started = False
        await self._pool.stop_sweeper()
        if self._config.tunnel_enabled:
            await self._tunnel.stop()
        return await self.status()

    async def status(self) -> dict[str, Any]:
        tunnel_status = await self._tunnel.status()
        return {
            "enabled": self._config.enabled,
            "active": self.active,
            "dry_run": self._dry_run,
            "fail_closed": self._config.fail_closed,
            "tunnel": tunnel_status.to_dict(),
            "pool": self._pool.health(),
            "problems": self.problems(),
        }

    # -- the one call callers need ----------------------------------------

    def resolve_proxy(
        self,
        session_key: Optional[str] = None,
        *,
        exclude: Optional[set[str]] = None,
        sticky: Optional[bool] = None,
    ) -> Optional[str]:
        """Return the proxy URL for this unit of work, or ``None`` for direct.

        ``sticky=True`` pins ``session_key`` to one endpoint regardless of the
        rotation policy — what a browser flow wants, since rotating mid-flow
        would break cookies and logins.

        Raises :class:`VPNUnavailable` when VPN is enabled, fail-closed, and no
        endpoint can serve the request.
        """
        if not self.active:
            return None
        try:
            return self._pool.select(session_key, exclude=exclude, sticky=sticky)
        except NoHealthyEndpoint as exc:
            if self._config.fail_closed:
                raise VPNUnavailable(str(exc)) from exc
            log.warning("vpn pool exhausted, falling back to direct: %s", exc)
            return None

    def report(
        self,
        url: Optional[str],
        *,
        ok: bool,
        latency_ms: Optional[float] = None,
        error: str = "",
    ) -> None:
        """Feed an outcome back so the pool can fail over on its own."""
        if not url:
            return
        self._pool.report(url, ok=ok, latency_ms=latency_ms, error=error)

    def describe(self) -> dict[str, Any]:
        """Redacted, JSON-safe snapshot for logs and the HTTP surface."""
        data = self._config.redacted()
        data["active"] = self.active
        data["pool_size"] = self._pool.size
        return data


# ---------------------------------------------------------------------------
# Module-level singleton, matching the engine's other process-wide services.
# ---------------------------------------------------------------------------

_manager: Optional[VPNManager] = None
_manager_lock = threading.Lock()


def get_vpn_manager(config: Optional[VPNConfig] = None) -> VPNManager:
    """Return the process-wide :class:`VPNManager`.

    The first call builds it from the environment; later calls reuse it unless
    an explicit ``config`` is passed (used by tests and by ``jambu vpn``).
    """
    global _manager
    with _manager_lock:
        if _manager is None or config is not None:
            _manager = VPNManager(config) if config is not None else VPNManager()
        return _manager


def reset_vpn_manager() -> None:
    """Drop the cached manager so the next call re-reads the environment."""
    global _manager
    with _manager_lock:
        _manager = None


def resolve_proxy(session_key: Optional[str] = None) -> Optional[str]:
    """Module-level shortcut: the proxy to use, or ``None`` for direct."""
    return get_vpn_manager().resolve_proxy(session_key)


def redact(url: str) -> str:
    """Re-exported so callers need not import two modules."""
    return redact_proxy_url(url)