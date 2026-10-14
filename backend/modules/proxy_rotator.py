"""
Proxy Rotator & Residential Proxy Pool Manager.

Provides:
1. Environment-driven and programmatic pool of residential/datacenter proxies.
2. Sticky session pinning (so multi-step browser flows keep the same IP).
3. Automatic rotation upon failure or anti-bot detection strike.
4. Playwright-compatible configuration dict generation.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

log = logging.getLogger("jambu.proxy_rotator")


def redact_proxy(url: str) -> str:
    """Masks credentials in proxy string for safe logging and telemetry."""
    if not url:
        return ""
    try:
        p = urlparse(url)
        if not p.username and not p.password:
            return url
        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"
        userinfo = "***:***@" if p.password else "***@"
        return urlunparse((p.scheme, f"{userinfo}{host}", p.path, "", p.query, ""))
    except Exception:
        return "invalid-proxy"


@dataclass
class ProxyEntry:
    url: str
    healthy: bool = True
    consecutive_failures: int = 0
    detections: int = 0
    last_used: float = 0.0
    quarantine_until: float = 0.0

    @property
    def available(self) -> bool:
        if not self.healthy:
            return False
        return time.time() >= self.quarantine_until

    def to_playwright_dict(self) -> Dict[str, str]:
        p = urlparse(self.url)
        server = f"{p.scheme}://{p.hostname}"
        if p.port:
            server += f":{p.port}"
        d: Dict[str, str] = {"server": server}
        if p.username:
            d["username"] = p.username
        if p.password:
            d["password"] = p.password
        return d


class ProxyRotator:
    """Thread-safe pool of residential/datacenter proxies with sticky session routing."""

    def __init__(self, proxy_urls: Optional[List[str]] = None):
        self._lock = threading.Lock()
        self._pool: List[ProxyEntry] = []
        self._session_pins: Dict[str, str] = {}  # session_id -> proxy_url
        self._round_robin_idx = 0

        # Load from argument or environment
        urls = proxy_urls or self._load_from_env()
        for u in urls:
            self.add_proxy(u)

    def _load_from_env(self) -> List[str]:
        raw = (
            os.getenv("JAMBU_RESIDENTIAL_PROXIES")
            or os.getenv("JAMBU_PROXY_POOL")
            or os.getenv("JAMBU_VPN_POOL")
            or ""
        )
        if not raw.strip():
            return []
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts

    def add_proxy(self, url: str) -> None:
        url = url.strip()
        if not url:
            return
        with self._lock:
            if not any(entry.url == url for entry in self._pool):
                self._pool.append(ProxyEntry(url=url))
                log.info("Registered proxy in pool: %s", redact_proxy(url))

    def remove_proxy(self, url: str) -> None:
        with self._lock:
            self._pool = [e for e in self._pool if e.url != url]

    def get_proxy(
        self, session_id: Optional[str] = None, *, rotate: bool = False
    ) -> Optional[Dict[str, str]]:
        """Resolves a proxy for the given session.

        Honors sticky session pinning unless rotate=True is requested.
        """
        with self._lock:
            if not self._pool:
                return None

            # Check sticky pin
            if session_id and not rotate:
                pinned_url = self._session_pins.get(session_id)
                if pinned_url:
                    entry = next((e for e in self._pool if e.url == pinned_url), None)
                    if entry and entry.available:
                        entry.last_used = time.time()
                        return entry.to_playwright_dict()

            # Filter available
            available = [e for e in self._pool if e.available]
            if not available:
                # If all are quarantined, pick the one closest to expiry
                available = sorted(self._pool, key=lambda x: x.quarantine_until)

            if not available:
                return None

            # Round robin pick
            chosen = available[self._round_robin_idx % len(available)]
            self._round_robin_idx += 1
            chosen.last_used = time.time()

            if session_id:
                self._session_pins[session_id] = chosen.url

            log.debug("Assigned proxy %s to session %s", redact_proxy(chosen.url), session_id)
            return chosen.to_playwright_dict()

    def record_detection(self, proxy_url_or_server: str) -> None:
        """Records an anti-bot detection strike (e.g. Cloudflare Turnstile hit)."""
        with self._lock:
            for entry in self._pool:
                if proxy_url_or_server in entry.url:
                    entry.detections += 1
                    # Short 60s quarantine to rotate to another IP
                    entry.quarantine_until = time.time() + 60.0
                    log.warning(
                        "Proxy %s triggered anti-bot detection (total strikes: %d). Quarantined 60s.",
                        redact_proxy(entry.url),
                        entry.detections,
                    )

    def record_failure(self, proxy_url_or_server: str, error: str = "") -> None:
        """Records a transport connection failure."""
        with self._lock:
            for entry in self._pool:
                if proxy_url_or_server in entry.url:
                    entry.consecutive_failures += 1
                    if entry.consecutive_failures >= 3:
                        entry.healthy = False
                        entry.quarantine_until = time.time() + 180.0
                    log.warning(
                        "Proxy %s failed (%s). Failures: %d",
                        redact_proxy(entry.url),
                        error[:100],
                        entry.consecutive_failures,
                    )

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total": len(self._pool),
                "healthy": sum(1 for e in self._pool if e.healthy),
                "available": sum(1 for e in self._pool if e.available),
                "pinned_sessions": len(self._session_pins),
                "proxies": [
                    {
                        "url": redact_proxy(e.url),
                        "healthy": e.healthy,
                        "available": e.available,
                        "detections": e.detections,
                        "failures": e.consecutive_failures,
                    }
                    for e in self._pool
                ],
            }


# Singleton instance
_instance: Optional[ProxyRotator] = None


def get_proxy_rotator() -> ProxyRotator:
    global _instance
    if _instance is None:
        _instance = ProxyRotator()
    return _instance
