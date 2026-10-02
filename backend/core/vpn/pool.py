"""
Dynamic proxy pool.

Sits *on top of* the tunnel layer and owns the part that actually makes egress
"dynamic": choosing which proxy endpoint a given unit of work uses, noticing
when one dies, and moving on without a human in the loop.

Behaviour:

* **Selection** honours the configured :class:`RotationPolicy` —
  ``failover`` (sticky), ``round_robin``, ``random``, ``least_latency``.
* **Health** is tracked per endpoint from real outcomes plus an optional
  background probe. After ``failure_threshold`` consecutive failures an
  endpoint is quarantined and skipped.
* **Sticky sessions** keep one endpoint per ``session_key`` for
  ``sticky_ttl`` seconds, so a browser session is not yanked between IPs
  mid-flow (which would break cookies and logins).

Thread-safe: the engine resolves egress from request handlers.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend.core.vpn.config import RotationPolicy, VPNConfig, redact_proxy_url

log = logging.getLogger("jambu.vpn.pool")


class NoHealthyEndpoint(RuntimeError):
    """No endpoint could serve the request.

    Raised when the pool is fail-closed. The message deliberately lists the
    redacted endpoints and their failure counts so an operator can act.
    """


@dataclass
class EndpointHealth:
    """Rolling health record for one proxy endpoint."""

    url: str
    region: str = ""
    healthy: bool = True
    consecutive_failures: int = 0
    successes: int = 0
    failures: int = 0
    latency_ms: Optional[float] = None
    last_checked: float = 0.0
    last_error: str = ""
    quarantined_until: float = 0.0

    @property
    def redacted_url(self) -> str:
        return redact_proxy_url(self.url)

    @property
    def available(self) -> bool:
        """Usable right now (not down, and any quarantine has expired)."""
        if not self.healthy:
            return False
        return time.time() >= self.quarantined_until

    def record_success(self, latency_ms: Optional[float] = None) -> None:
        self.successes += 1
        self.consecutive_failures = 0
        self.healthy = True
        self.quarantined_until = 0.0
        self.last_checked = time.time()
        self.last_error = ""
        if latency_ms is not None:
            self.latency_ms = (
                latency_ms
                if self.latency_ms is None
                else self.latency_ms * 0.7 + latency_ms * 0.3  # EWMA
            )

    def record_failure(self, error: str, threshold: int) -> None:
        self.failures += 1
        self.consecutive_failures += 1
        self.last_checked = time.time()
        self.last_error = error[:200]
        if self.consecutive_failures >= threshold:
            self.healthy = False
            # Short backoff so a transient blip self-heals without a restart.
            self.quarantined_until = time.time() + min(
                30.0, 2.0 * self.consecutive_failures
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.redacted_url,
            "region": self.region,
            "healthy": self.healthy,
            "available": self.available,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "latency_ms": round(self.latency_ms, 1)
            if self.latency_ms is not None
            else None,
            "last_checked": self.last_checked,
            "last_error": self.last_error,
        }


class ProxyPool:
    """A rotating, health-tracked set of proxy endpoints."""

    def __init__(
        self,
        config: Optional[VPNConfig] = None,
        *,
        probe: Optional[Callable[[str], Any]] = None,
    ):
        self._config = config or VPNConfig()
        self._probe = probe
        self._lock = threading.Lock()
        self._index = 0
        self._current: Optional[str] = None
        self._sticky: dict[str, tuple[str, float]] = {}
        self._endpoints: dict[str, EndpointHealth] = {}
        self._regions: dict[str, str] = {}
        self._sweeper: Optional[asyncio.Task] = None
        for url in self._config.pool:
            self._endpoints[url] = EndpointHealth(url=url)

    # -- membership --------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._endpoints)

    def urls(self) -> list[str]:
        return list(self._endpoints)

    def set_region(self, url: str, region: str) -> None:
        """Tag an endpoint with a region for geo-aware selection."""
        if url in self._endpoints:
            self._regions[url] = region
            self._endpoints[url].region = region

    def add(self, url: str, region: str = "") -> None:
        with self._lock:
            if url not in self._endpoints:
                self._endpoints[url] = EndpointHealth(url=url, region=region)
            if region:
                self._regions[url] = region
                self._endpoints[url].region = region

    def remove(self, url: str) -> bool:
        with self._lock:
            existed = self._endpoints.pop(url, None) is not None
            self._regions.pop(url, None)
            for key, (pinned, _) in list(self._sticky.items()):
                if pinned == url:
                    self._sticky.pop(key, None)
            return existed

    def _candidates(self) -> list[EndpointHealth]:
        allowed = set(self._config.allowed_regions)
        return [
            ep
            for ep in self._endpoints.values()
            if (not allowed or ep.region in allowed) and ep.available
        ]

    # -- selection ---------------------------------------------------------

    def select(
        self,
        session_key: Optional[str] = None,
        *,
        exclude: Optional[set[str]] = None,
        sticky: Optional[bool] = None,
    ) -> str:
        """Return the proxy URL to use.

        ``session_key`` pins a caller to one endpoint for ``sticky_ttl``.
        Raises :class:`NoHealthyEndpoint` when nothing is usable.

        ``sticky`` forces pinning on or off. It defaults to on for
        ``failover`` and off otherwise, so ``round_robin`` still rotates —
        but a browser flow can pass ``sticky=True`` to guarantee one egress IP
        for the whole session regardless of policy. Rotating mid-flow would
        break cookies and logins.
        """
        excluded = exclude or set()
        now = time.time()
        if sticky is None:
            sticky = self._config.rotation is RotationPolicy.FAILOVER

        with self._lock:
            # Sticky path: honour the pin unless it is now unusable.
            if session_key and sticky:
                pinned = self._sticky.get(session_key)
                if pinned and pinned[1] > now:
                    url = pinned[0]
                    ep = self._endpoints.get(url)
                    if ep and ep.available and url not in excluded:
                        return url
                    self._sticky.pop(session_key, None)

            pool = [e for e in self._candidates() if e.url not in excluded]
            if not pool:
                raise NoHealthyEndpoint(self._unavailable_reason(excluded))

            chosen = self._pick(pool)
            if session_key:
                self._sticky[session_key] = (
                    chosen.url,
                    now + self._config.sticky_ttl,
                )
            return chosen.url

    def _pick(self, pool: list[EndpointHealth]) -> EndpointHealth:
        policy = self._config.rotation
        if policy is RotationPolicy.RANDOM:
            return random.choice(pool)
        if policy is RotationPolicy.LEAST_LATENCY:
            measured = [e for e in pool if e.latency_ms is not None]
            if measured:
                return min(measured, key=lambda e: e.latency_ms or 0.0)
            return random.choice(pool)
        if policy is RotationPolicy.ROUND_ROBIN:
            ordered = sorted(pool, key=lambda e: e.url)
            pick = ordered[self._index % len(ordered)]
            self._index = (self._index + 1) % max(len(ordered), 1)
            return pick
        # FAILOVER: stay on the endpoint that last worked, else take the first.
        if self._current is not None:
            for ep in pool:
                if ep.url == self._current:
                    return ep
        return pool[0]

    def _unavailable_reason(self, exclude: set[str]) -> str:
        if not self._endpoints:
            return "VPN pool is empty"
        detail = ", ".join(
            f"{ep.redacted_url}({ep.consecutive_failures} fail)"
            for ep in self._endpoints.values()
            if ep.url not in exclude
        )
        return f"no healthy VPN endpoint available: {detail or 'all excluded'}"

    # -- outcome reporting -------------------------------------------------

    def report(
        self,
        url: str,
        *,
        ok: bool,
        latency_ms: Optional[float] = None,
        error: str = "",
    ) -> None:
        """Feed a real request outcome back into the health model."""
        with self._lock:
            ep = self._endpoints.get(url)
            if ep is None:
                return
            if ok:
                ep.record_success(latency_ms)
                self._current = url
            else:
                ep.record_failure(
                    error or "request failed", self._config.failure_threshold
                )
                if not ep.healthy:
                    log.warning(
                        "vpn endpoint %s quarantined after %d consecutive failures: %s",
                        ep.redacted_url,
                        ep.consecutive_failures,
                        ep.last_error,
                    )

    def health(self) -> dict[str, Any]:
        """Full pool health, safe to return over HTTP."""
        with self._lock:
            endpoints = [ep.to_dict() for ep in self._endpoints.values()]
            sticky = len(self._sticky)
        healthy = sum(1 for ep in endpoints if ep["available"])
        return {
            "size": len(endpoints),
            "healthy": healthy,
            "unhealthy": len(endpoints) - healthy,
            "rotation": self._config.rotation.value,
            "sticky_sessions": sticky,
            "endpoints": endpoints,
        }

    # -- background probing ------------------------------------------------

    async def probe_once(self) -> dict[str, Any]:
        """Probe every endpoint once, concurrently.

        Uses the injected ``probe`` callable when present (tests inject a fake);
        otherwise performs a real request through each proxy when
        ``health_probe_url`` is set. Endpoints with no probe configured are
        left untouched rather than guessed at.
        """
        if self._probe is None and not self._config.health_probe_url:
            return {"probed": 0, "reason": "no health probe configured"}

        async def run(url: str) -> tuple[str, bool, Optional[float], str]:
            started = time.monotonic()
            try:
                if self._probe is not None:
                    result = self._probe(url)
                    if asyncio.iscoroutine(result):
                        await result
                else:
                    await self._http_probe(url)
                elapsed = (time.monotonic() - started) * 1000.0
                return url, True, elapsed, ""
            except Exception as exc:  # noqa: BLE001 - any failure is a failure
                return url, False, None, f"{type(exc).__name__}: {exc}"

        urls = self.urls()
        results = await asyncio.gather(*(run(u) for u in urls))
        for url, ok, latency, error in results:
            self.report(url, ok=ok, latency_ms=latency, error=error)
        return {"probed": len(urls)}

    async def _http_probe(self, url: str) -> None:
        """Fetch the configured probe URL through one proxy endpoint."""
        from backend.core.socks import make_async_client

        client = make_async_client(
            timeout=self._config.health_timeout, proxy_url=url
        )
        try:
            response = await client.get(self._config.health_probe_url)
            response.raise_for_status()
        finally:
            await client.aclose()

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.health_interval)
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a sweep must never kill the loop
                log.exception("vpn health sweep failed")

    def start_sweeper(self) -> bool:
        """Start the background probe loop. Returns False if already running."""
        if self._sweeper is not None and not self._sweeper.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop yet (startup before serve) — caller retries later.
            return False
        self._sweeper = loop.create_task(self._sweep_loop())
        return True

    async def stop_sweeper(self) -> None:
        task = self._sweeper
        self._sweeper = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass