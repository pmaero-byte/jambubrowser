"""
Tunnel layer: a system-level VPN that forms the *base* egress.

This wraps WireGuard and OpenVPN behind one interface so the pool above can
sit on top of it. A tunnel is bring-up/bring-down only — rotation and health
of individual endpoints is the pool's job.

**Privilege.** Managing a tunnel requires root and the vendor binary
(``wg-quick`` / ``openvpn``). Everything here checks for that up front and
fails with an actionable message rather than a traceback. A
:class:`NullTunnel` keeps the code path exercisable in tests and CI where
neither binary nor root exists.

Nothing runs at import time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from backend.core.vpn.config import TunnelKind, VPNConfig

log = logging.getLogger("jambu.vpn.tunnel")


class TunnelState(str, Enum):
    DOWN = "down"
    UP = "up"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class TunnelError(RuntimeError):
    """Raised when a tunnel cannot be established or torn down."""


@dataclass
class TunnelStatus:
    """Snapshot of the tunnel layer, safe to return over HTTP."""

    kind: str = TunnelKind.NONE.value
    state: str = TunnelState.DOWN.value
    interface: str = ""
    endpoint: str = ""
    dns: list[str] = field(default_factory=list)
    since: float = 0.0
    last_error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "state": self.state,
            "interface": self.interface,
            "endpoint": self.endpoint,
            "dns": list(self.dns),
            "up_for_seconds": round(time.time() - self.since, 1) if self.since else 0.0,
            "last_error": self.last_error,
            "detail": dict(self.detail),
        }


class TunnelBackend(ABC):
    """Bring a single VPN tunnel up or down."""

    kind: str = TunnelKind.NONE.value

    @abstractmethod
    async def up(self) -> TunnelStatus: ...

    @abstractmethod
    async def down(self) -> TunnelStatus: ...

    @abstractmethod
    async def status(self) -> TunnelStatus: ...

    @staticmethod
    def _have_root() -> bool:
        return hasattr(os, "geteuid") and os.geteuid() == 0


class NullTunnel(TunnelBackend):
    """No-op tunnel.

    Used when no tunnel is configured, and as the CI/test backend: ``up()``
    succeeds and records intent without touching the host. Keeping this a real
    object (rather than ``None``) means the caller's code path is identical.
    """

    kind = TunnelKind.NONE.value

    def __init__(self, config: Optional[VPNConfig] = None):
        self._config = config or VPNConfig()
        self._status = TunnelStatus(kind=self.kind, state=TunnelState.DOWN.value)

    async def up(self) -> TunnelStatus:
        self._status = TunnelStatus(
            kind=self.kind,
            state=TunnelState.UP.value,
            interface=self._config.tunnel_interface,
            endpoint=self._config.tunnel_endpoint,
            dns=list(self._config.tunnel_dns),
            since=time.time(),
            detail={"simulated": True},
        )
        return self._status

    async def down(self) -> TunnelStatus:
        self._status = TunnelStatus(kind=self.kind, state=TunnelState.DOWN.value)
        return self._status

    async def status(self) -> TunnelStatus:
        return self._status


class _CommandTunnel(TunnelBackend):
    """Shared subprocess plumbing for the real vendor tools."""

    binary: str = ""

    def __init__(self, config: VPNConfig, *, dry_run: bool = False):
        self._config = config
        self._dry_run = dry_run
        self._status = TunnelStatus(
            kind=self.kind,
            state=TunnelState.DOWN.value,
            interface=config.tunnel_interface,
            endpoint=config.tunnel_endpoint,
            dns=list(config.tunnel_dns),
        )

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def _blocked_reason(self) -> str:
        """Why this tunnel cannot run here, or "" if it can."""
        if self._dry_run:
            return ""
        # Every binary this backend shells out to, not just the launcher:
        # status() may invoke a different tool (e.g. `wg` vs `wg-quick`).
        missing = [b for b in self.required_binaries() if shutil.which(b) is None]
        if missing:
            return (
                f"{', '.join(missing)} not installed; install the VPN client or set "
                f"JAMBU_VPN_DRY_RUN=1 to simulate"
            )
        if not self._have_root():
            return (
                f"managing a {self.kind} tunnel needs root; run with elevated "
                f"privileges or set JAMBU_VPN_DRY_RUN=1 to simulate"
            )
        return ""

    @classmethod
    def required_binaries(cls) -> tuple[str, ...]:
        """Binaries that must exist for this backend to work."""
        return (cls.binary,)

    async def _run(self, argv: list[str]) -> tuple[int, str, str]:
        """Run a command, or log-and-succeed when dry-run is on.

        Every shell-out goes through here, so this is also the choke point
        that refuses to spawn a vendor binary we do not have (or lack root to
        use). Callers get a non-zero code and an explanatory message instead
        of a ``FileNotFoundError`` traceback.
        """
        blocked = self._blocked_reason()
        if blocked:
            return 127, "", blocked
        if self._dry_run:
            log.info("[vpn dry-run] %s", " ".join(argv))
            return 0, "", ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            raise TunnelError(f"failed to launch {argv[0]}: {exc}") from exc
        out, err = await proc.communicate()
        return (
            proc.returncode or 0,
            out.decode(errors="replace"),
            err.decode(errors="replace"),
        )

    def _fail(self, reason: str) -> TunnelStatus:
        self._status = TunnelStatus(
            kind=self.kind,
            state=TunnelState.UNAVAILABLE.value,
            interface=self._config.tunnel_interface,
            endpoint=self._config.tunnel_endpoint,
            dns=list(self._config.tunnel_dns),
            last_error=reason,
        )
        return self._status


class WireGuardTunnel(_CommandTunnel):
    """Bring a WireGuard interface up/down with ``wg-quick``."""

    kind = TunnelKind.WIREGUARD.value
    binary = "wg-quick"

    @classmethod
    def required_binaries(cls) -> tuple[str, ...]:
        # status() shells out to `wg` as well as `wg-quick`.
        return ("wg-quick", "wg")

    async def up(self) -> TunnelStatus:
        blocked = self._blocked_reason()
        if blocked:
            return self._fail(blocked)
        iface = self._config.tunnel_interface
        argv = ["wg-quick", "up", self._config.tunnel_config_path or iface]
        code, _, err = await self._run(argv)
        if code != 0:
            return self._fail(err.strip() or f"wg-quick up failed ({code})")
        self._status = TunnelStatus(
            kind=self.kind,
            state=TunnelState.UP.value,
            interface=iface,
            endpoint=self._config.tunnel_endpoint,
            dns=list(self._config.tunnel_dns),
            since=time.time(),
            detail={"simulated": self._dry_run},
        )
        return self._status

    async def down(self) -> TunnelStatus:
        blocked = self._blocked_reason()
        if blocked:
            return self._fail(blocked)
        code, _, err = await self._run(
            ["wg-quick", "down", self._config.tunnel_interface]
        )
        if code != 0:
            return self._fail(err.strip() or f"wg-quick down failed ({code})")
        self._status.state = TunnelState.DOWN.value
        self._status.since = 0.0
        return self._status

    async def status(self) -> TunnelStatus:
        if not self._config.tunnel_interface:
            return self._fail("no interface configured")
        blocked = self._blocked_reason()
        if blocked:
            return self._fail(blocked)
        code, out, err = await self._run(["wg", "show", self._config.tunnel_interface])
        if code != 0:
            self._status.state = TunnelState.DOWN.value
            self._status.last_error = err.strip()[:200]
        else:
            self._status.state = TunnelState.UP.value
            self._status.detail["has_output"] = bool(out.strip())
        return self._status


class OpenVPNTunnel(_CommandTunnel):
    """Bring an OpenVPN tunnel up/down with the ``openvpn`` client."""

    kind = TunnelKind.OPENVPN.value
    binary = "openvpn"

    @classmethod
    def required_binaries(cls) -> tuple[str, ...]:
        # down() uses pkill to stop the detached daemon.
        return ("openvpn", "pkill")

    async def up(self) -> TunnelStatus:
        blocked = self._blocked_reason()
        if blocked:
            return self._fail(blocked)
        cfg = self._config.tunnel_config_path
        if not cfg:
            return self._fail("JAMBU_VPN_TUNNEL_CONFIG is required for OpenVPN")
        # --daemon detaches, so we do not hold the event loop open.
        code, _, err = await self._run(["openvpn", "--config", cfg, "--daemon"])
        if code != 0:
            return self._fail(err.strip() or f"openvpn failed ({code})")
        self._status = TunnelStatus(
            kind=self.kind,
            state=TunnelState.UP.value,
            interface=self._config.tunnel_interface,
            endpoint=self._config.tunnel_endpoint,
            dns=list(self._config.tunnel_dns),
            since=time.time(),
            detail={"config": cfg, "simulated": self._dry_run},
        )
        return self._status

    async def down(self) -> TunnelStatus:
        blocked = self._blocked_reason()
        if blocked:
            return self._fail(blocked)
        code, _, err = await self._run(
            ["pkill", "-TERM", "-f", f"openvpn.*{self._config.tunnel_config_path}"]
        )
        if code not in (0, 1):  # 1 == nothing matched, which is fine
            return self._fail(err.strip() or f"openvpn shutdown failed ({code})")
        self._status.state = TunnelState.DOWN.value
        self._status.since = 0.0
        return self._status

    async def status(self) -> TunnelStatus:
        # We deliberately do not adopt the daemon process, so we report the
        # last known state rather than probing for a process we do not own.
        return self._status


_BACKENDS = {
    TunnelKind.WIREGUARD: WireGuardTunnel,
    TunnelKind.OPENVPN: OpenVPNTunnel,
}


def build_backend(config: VPNConfig, *, dry_run: bool = False) -> TunnelBackend:
    """Pick the backend for ``config``; falls back to :class:`NullTunnel`."""
    if not config.tunnel_enabled:
        return NullTunnel(config)
    factory = _BACKENDS.get(config.tunnel_kind)
    if factory is None:
        return NullTunnel(config)
    return factory(config, dry_run=dry_run)  # type: ignore[call-arg]


class TunnelManager:
    """Owns the lifecycle of the single base tunnel.

    The tunnel is a process-wide resource (one network interface), so this
    mirrors the engine's other singletons: one instance, explicit ``start`` /
    ``stop``, and thread-safe state.
    """

    def __init__(self, config: Optional[VPNConfig] = None, *, dry_run: bool = False):
        self._config = config or VPNConfig()
        self._dry_run = dry_run
        self._backend: TunnelBackend = build_backend(self._config, dry_run=dry_run)
        self._lock = asyncio.Lock()

    @property
    def config(self) -> VPNConfig:
        return self._config

    @property
    def backend(self) -> TunnelBackend:
        return self._backend

    @property
    def enabled(self) -> bool:
        return self._config.tunnel_enabled

    async def start(self) -> TunnelStatus:
        """Bring the tunnel up. Idempotent."""
        async with self._lock:
            # Trust our own recorded state before shelling out to probe:
            # a dry-run or a freshly-built backend would otherwise be
            # overwritten by a status() that reports the host, not intent.
            remembered = getattr(self._backend, "_status", None)
            if remembered is not None and remembered.state == TunnelState.UP.value:
                return remembered
            return await self._backend.up()

    async def stop(self) -> TunnelStatus:
        async with self._lock:
            return await self._backend.down()

    async def status(self) -> TunnelStatus:
        return await self._backend.status()

    def replace_config(self, config: VPNConfig) -> None:
        """Swap configuration, tearing the old tunnel down conceptually.

        Only legal while down — an interface cannot be re-pointed underneath
        live traffic, so a caller must ``stop()`` first.
        """
        current = self._backend
        self._config = config
        self._backend = build_backend(config, dry_run=self._dry_run)
        if getattr(current, "_status", None) and current._status.state == TunnelState.UP.value:
            log.warning(
                "vpn config replaced while tunnel %s was up; "
                "call stop() before start() to avoid a leaked interface",
                current._status.interface,
            )