"""
Page fetching with optional JavaScript rendering
===============================================

Plain HTTP is the right default: it is fast, cheap and works for most of the
web. It is also wrong for a single-page app, where the server returns a shell
and the content arrives later from JavaScript.

So there are two transports and a policy:

* ``never``  — HTTP only.
* ``always`` — drive the real browser (Playwright, via the engine's scraper).
* ``auto`` (default) — HTTP first, then escalate to the browser **only if the
  response looks like a JavaScript shell**.

``auto`` is the default because rendering costs a browser launch per page, and
paying that for a static article is waste. The escalation decision is a
heuristic, and it is documented as one: it looks for a large document with
almost no readable text, or an empty SPA mount point. It will get some pages
wrong in both directions, which is why ``always`` exists as the escape hatch.

Both transports are injectable so the policy is testable without a browser or a
network.
"""

from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable, Dict, Optional, Tuple

log = logging.getLogger("jambu.fetcher")

RenderMode = str  # "never" | "auto" | "always"
VALID_RENDER_MODES = ("never", "auto", "always")

HttpGet = Callable[..., Awaitable[Tuple[int, str]]]
BrowserGet = Callable[..., Awaitable[Optional[str]]]

# A document with no readable text below this many characters is treated as a
# shell, but only if the document itself is substantial (see looks_js_shell).
_MIN_READABLE_CHARS = 200
_MIN_DOCUMENT_BYTES = 500
_SPA_MOUNT_MARKERS = (
    'id="root"', "id='root'",
    'id="app"', "id='app'",
    'id="__next"', "id='__next'",
    'id="___gatsby"',
)

_STRIP_BLOCKS_RE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>", re.S | re.I
)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def readable_text(html: str) -> str:
    """The human-visible text of a document, roughly.

    Mirrors the sanitiser's approach (drop script/style/noscript/template, then
    tags) without pulling in a parser, because this only needs to answer "is
    there anything here to read".
    """
    if not html:
        return ""
    text = _STRIP_BLOCKS_RE.sub(" ", html)
    text = _STRIP_TAGS_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def looks_js_shell(html: str) -> bool:
    """Heuristic: did we receive a shell rather than the content?

    Two signals, both deliberately conservative so we do not launch a browser
    for ordinary pages:

    * a substantial document containing almost no readable text;
    * an SPA mount point that is still empty.
    """
    if not html or len(html) < _MIN_DOCUMENT_BYTES:
        return False

    text = readable_text(html)
    if len(text) < _MIN_READABLE_CHARS:
        return True

    lowered = html
    for marker in _SPA_MOUNT_MARKERS:
        if marker in lowered and len(text) < 600:
            return True
    return False


async def fetch_page(
    url: str,
    *,
    render: RenderMode = "auto",
    max_age: float = 0.0,
    http_get: Optional[HttpGet] = None,
    browser_get: Optional[BrowserGet] = None,
) -> Dict[str, object]:
    """Fetch a page, choosing a transport.

    Returns a dict: ``{status, body, transport, rendered, note?}``.
    ``transport`` is ``"http"`` or ``"browser"``; ``rendered`` says whether the
    body came from a JavaScript-capable engine.
    """
    mode = render if render in VALID_RENDER_MODES else "auto"

    http_get = http_get or _default_http_get
    browser_get = browser_get or _default_browser_get

    if mode == "always":
        body = await _try_browser(url, browser_get)
        if body is not None:
            return {"status": 200, "body": body, "transport": "browser", "rendered": True}
        # Browser unavailable: fall back rather than fail the request.
        status, body = await http_get(url, max_age)
        return {
            "status": status, "body": body, "transport": "http", "rendered": False,
            "note": "browser rendering was requested but unavailable; served over HTTP",
        }

    status, body = await http_get(url, max_age)

    if mode == "never" or not looks_js_shell(body or ""):
        return {"status": status, "body": body, "transport": "http", "rendered": False}

    rendered_body = await _try_browser(url, browser_get)
    if rendered_body is None:
        return {
            "status": status, "body": body, "transport": "http", "rendered": False,
            "note": "page looked like a JavaScript shell but the browser was unavailable",
        }
    return {
        "status": 200, "body": rendered_body, "transport": "browser", "rendered": True,
        "note": "escalated to the browser: the HTTP response looked like a JS shell",
    }


async def _try_browser(url: str, browser_get: BrowserGet) -> Optional[str]:
    """Run the browser transport, returning None when it is unavailable."""
    try:
        html = await browser_get(url)
    except Exception as e:  # noqa: BLE001 — a missing browser is not a page error
        log.info("browser transport unavailable for %s: %s", url, e)
        return None
    return html or None


# ---------------------------------------------------------------------------
# Default transports
# ---------------------------------------------------------------------------

async def _default_http_get(url: str, max_age: float = 0.0) -> Tuple[int, str]:
    from backend.routes.webdata import _http_fetch

    return await _http_fetch(url, max_age)


async def _default_browser_get(url: str) -> Optional[str]:
    """Render with the engine's own scraper (crawl4ai → Playwright).

    Imported lazily: crawl4ai and Playwright are optional dependencies, and the
    web-data surface must keep working over plain HTTP without them.
    """
    from backend.modules.scraper import scrape_url as do_scrape

    result = await do_scrape(url)
    if isinstance(result, dict):
        # Prefer the rendered HTML so link extraction sees the real DOM; fall
        # back to markdown when the engine only returned text.
        return result.get("html") or result.get("markdown") or None
    return str(result) if result else None
