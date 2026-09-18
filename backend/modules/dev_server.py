"""
Local dev-server awareness for agent test flows.

Two jobs:

1. **Find** a running dev server (scan common ports) and identify the
   framework from response headers/markup.
2. **Settle** — after a navigation, wait until the dev server's hot-reload
   churn stops, so flows don't race a rebuild.

Everything here is best-effort and never raises into a flow; the caller gets
``reachable: false`` when nothing is listening.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("jambu.dev_server")

# Common dev-server ports across ecosystems (Vite, CRA, Next, Astro, Nuxt,
# Angular, Django/Flask, generic static servers, and this engine's own 8001).
COMMON_PORTS = (1420, 3000, 3001, 4200, 4321, 5000, 5173, 5174, 8000, 8080, 8081)

# (framework, markers) — matched case-insensitively against headers + body.
FRAMEWORK_SIGNATURES = (
    ("vite", ("/@vite/client", "vite/", "import.meta.hot")),
    ("next", ("__next", "/_next/", "x-powered-by: next.js")),
    ("nuxt", ("__nuxt", "/_nuxt/")),
    ("create-react-app", ("react-scripts", "/static/js/bundle.js")),
    ("remix", ("__remixcontext", "/build/")),
    ("sveltekit", ("__sveltekit", "@sveltejs/kit")),
    ("astro", ("data-astro", "astro-island")),
    ("angular", ("ng-version", "angular")),
    ("webpack", ("__webpack_require__", "webpackjsonp")),
    ("django", ("csrfmiddlewaretoken", "x-frame-options")),
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def detect_framework(headers: dict, body: str = "") -> Optional[str]:
    """Identify the dev framework from response headers + HTML body."""
    blob = " ".join(f"{k}: {v}" for k, v in (headers or {}).items()).lower()
    blob += " " + (body or "").lower()
    for framework, markers in FRAMEWORK_SIGNATURES:
        if any(marker in blob for marker in markers):
            return framework
    if "server" in (headers or {}):
        server = str(headers.get("server", "")).lower()
        if "django" in server:
            return "django"
    return None


def extract_title(body: str) -> str:
    match = _TITLE_RE.search(body or "")
    return match.group(1).strip()[:200] if match else ""


def _base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


async def probe_url(url: str, timeout: float = 2.0) -> dict:
    """Fetch a URL and report reachability, framework, title, server header."""
    try:
        import httpx
    except Exception:  # pragma: no cover - httpx is a core dep
        return {"url": url, "reachable": False, "error": "httpx unavailable"}
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, verify=False,
        ) as client:
            resp = await client.get(url)
        headers = {k.lower(): v for k, v in resp.headers.items()}
        body = resp.text[:20000] if resp.headers.get("content-type", "").startswith("text/html") else ""
        return {
            "url": str(resp.url),
            "reachable": True,
            "status": resp.status_code,
            "framework": detect_framework(headers, body),
            "title": extract_title(body),
            "server": headers.get("server"),
        }
    except Exception as exc:
        return {"url": url, "reachable": False, "error": str(exc)[:200]}


async def wait_for_server(url: str, timeout: float = 10.0,
                          interval: float = 0.25) -> bool:
    """Poll until the URL responds (used before a flow to avoid flake)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await probe_url(url, timeout=min(2.0, interval + 0.5))
        if result.get("reachable") and result.get("status", 500) < 500:
            return True
        await asyncio.sleep(interval)
    return False


async def scan_local_ports(host: str = "127.0.0.1",
                           ports: Optional[tuple] = None,
                           timeout: float = 0.5) -> list[dict]:
    """Probe common dev ports in parallel; return the reachable ones."""
    ports = tuple(ports or COMMON_PORTS)

    async def one(port: int) -> Optional[dict]:
        result = await probe_url(_base_url(host, port), timeout=timeout)
        if result.get("reachable") and result.get("status", 500) < 500:
            result["port"] = port
            result["host"] = host
            return result
        return None

    found = await asyncio.gather(*(one(p) for p in ports))
    return [item for item in found if item]


def is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
