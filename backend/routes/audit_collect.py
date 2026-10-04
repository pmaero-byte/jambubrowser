"""The phases of one audit page load.

`collect_page_data` was a 268-line function: launch, four listener closures,
navigation, two screenshots, an accessibility snapshot with a DOM fallback,
page source, a hand-rolled "lighthouse-lite", teardown and an audit-trail write.
One function, one traceback, and no way to re-run a single phase.

Each phase is now its own function that takes the Playwright page and the
`AuditData` it fills, in the same order as before. The rule that made this
safe to lift out: **a failed phase never fails the audit.** Every phase keeps its
own try/except and its original log level, because a missing screenshot is not a
reason to return no data at all.

The listeners become a `NetworkCapture` record rather than three closures plus a
`nonlocal`. That makes teardown symmetric with attach (the `detach` callable is
built where the listeners are attached) and means the counts the audit trail
writes are read from the same object the listeners fill.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from backend.core.audit import ActionCategory, get_audit_logger
from backend.employees.base import AuditData

if TYPE_CHECKING:  # avoids a cycle: the request model lives with the routes
    from backend.routes.audit import AuditCollectRequest

log = logging.getLogger("jambu.audit_collect")


@dataclass
class NetworkCapture:
    """What the response/console listeners saw while the page loaded.

    Filled by the handlers attached in :func:`attach_collectors`, read by the
    phases that report it. One object rather than three lists and a nonlocal,
    so there is exactly one thing to hand to the next phase.
    """

    requests: list[dict] = field(default_factory=list)
    response_headers: dict[str, str] = field(default_factory=dict)
    cookies: list[dict] = field(default_factory=list)
    console_logs: list[dict] = field(default_factory=list)
    detach: Optional[Callable[[], None]] = None


async def attach_collectors(page, context) -> NetworkCapture:
    """Attach the response and console listeners; return their capture record.

    Must run *before* navigation, or the initial document response and the
    requests that decide the load time are missed. Timing fields are read from
    the request timing object and default to -1 when the browser did not
    report them, which the scorer treats as "unknown", not "fast".
    """
    capture = NetworkCapture()
    # The lists live on the capture record the caller holds.

    async def on_response(response):
        req = response.request
        timing = {}
        try:
            t = response.request.timing
            if t:
                timing = {
                    "start_time": t.get("startTime", 0),
                    "dns": t.get("dnsEnd", 0) - t.get("dnsStart", 0) if t.get("dnsEnd", -1) >= 0 else -1,
                    "connect": t.get("connectEnd", 0) - t.get("connectStart", 0) if t.get("connectEnd", -1) >= 0 else -1,
                    "ttfb": t.get("receiveHeadersEnd", 0) - t.get("sendEnd", 0) if t.get("receiveHeadersEnd", -1) >= 0 else -1,
                    "total": t.get("responseEnd", 0) - t.get("startTime", 0) if t.get("responseEnd", -1) >= 0 else -1,
                }
        except Exception:
            log.debug("audit: ignored failure", exc_info=True)

        capture.requests.append({
            "url": req.url,
            "method": req.method,
            "status": response.status,
            "status_text": response.status_text,
            "resource_type": req.resource_type,
            "transfer_size": int(response.headers.get("content-length", 0) or 0),
            "timing": timing,
        })

    async def on_main_response(response):
        if response.request.resource_type == "document":
            capture.response_headers = dict(response.headers)
            try:
                page_cookies = await context.cookies()
                capture.cookies.extend(page_cookies)
            except Exception:
                log.debug("audit: ignored failure", exc_info=True)

    async def on_console(msg):
        capture.console_logs.append({
            "level": msg.type,
            "text": msg.text,
            "location": f"{msg.location.get('url','')}:{msg.location.get('lineNumber','')}" if msg.location else "",
        })

    page.on("response", on_response)
    page.on("response", on_main_response)
    page.on("console", on_console)

    # Teardown is built here, beside the attach, so the two cannot drift.
    def detach():
        page.remove_listener("response", on_response)
        page.remove_listener("response", on_main_response)
        page.remove_listener("console", on_console)

    capture.detach = detach
    return capture


async def navigate_and_time(
    page,
    data: AuditData,
    req: "AuditCollectRequest",
    capture: NetworkCapture,
    start_time: float,
) -> None:
    """Load the URL, record the title, and stamp the viewport and load time.

    A navigation failure is logged and swallowed: the page may still have
    loaded partially, and a partially-rendered page is worth auditing (it is
    exactly when the DOM fallback below earns its keep).
    """
    try:
        main_response = await page.goto(
            req.url,
            wait_until="networkidle",
            timeout=req.timeout_ms,
        )
        if main_response:
            capture.response_headers.update(dict(main_response.headers))
            data.title = await page.title()

        # Small extra wait for late-loading resources
        await asyncio.sleep(1.0)
    except Exception as e:
        log.warning("Navigation to %s had issues: %s", req.url, e)
        try:
            data.title = await page.title()
        except Exception:
            log.debug("audit: ignored failure", exc_info=True)

    data.load_time_ms = (time.time() - start_time) * 1000
    data.viewport_width = req.width
    data.viewport_height = req.height


async def collect_screenshots(page, data: AuditData, req: "AuditCollectRequest") -> None:
    """Take the viewport and (optional) full-page screenshots as base64."""
    if req.capture_screenshot:
        try:
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            data.screenshot_base64 = base64.b64encode(screenshot_bytes).decode()
        except Exception as e:
            log.warning("Screenshot failed: %s", e)

    if req.capture_fullpage:
        try:
            fp_bytes = await page.screenshot(type="png", full_page=True)
            data.fullpage_screenshot_base64 = base64.b64encode(fp_bytes).decode()
        except Exception as e:
            log.warning("Fullpage screenshot failed: %s", e)


async def collect_dom_snapshot(page, data: AuditData) -> None:
    """Fill ``dom_snapshot`` from the accessibility tree, else from the DOM.

    The accessibility tree is the better source (it is what a screen reader
    sees, so it reflects the app rather than the markup), but it is empty for
    some pages. The fallback evaluates a small script that pulls headings,
    links, buttons, inputs, images, forms and meta tags out of the DOM, so an
    audit always has *some* structure to reason about.
    """
    try:
        snapshot = await page.accessibility.snapshot()
        if snapshot:
            data.dom_snapshot = _format_accessibility_tree(snapshot)
    except Exception as e:
        log.warning("Accessibility snapshot failed: %s", e)

    # Fallback: extract basic DOM structure if accessibility tree is empty
    if not data.dom_snapshot:
        try:
            dom_info = await page.evaluate("""() => {
                const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(
                    h => h.tagName + ': ' + (h.textContent || '').trim().substring(0, 80)
                );
                const links = Array.from(document.querySelectorAll('a[href]')).map(
                    a => a.textContent.trim().substring(0, 60) + ' → ' + a.href.substring(0, 100)
                );
                const buttons = Array.from(document.querySelectorAll('button,input[type=submit],input[type=button]')).map(
                    b => (b.textContent || b.value || b.id || 'unnamed').trim().substring(0, 50)
                );
                const inputs = Array.from(document.querySelectorAll('input,textarea,select')).map(
                    i => `${i.tagName}[type=${i.type || 'text'}] name=${i.name || '?'} id=${i.id || '?'}`.substring(0, 80)
                );
                const images = Array.from(document.querySelectorAll('img')).map(
                    img => `src=${img.src.substring(0, 60)} alt=\"${(img.alt || '').substring(0, 40)}\"`
                );
                const forms = Array.from(document.querySelectorAll('form')).map(
                    f => `action=${f.action.substring(0, 60)} method=${f.method}`
                );
                const meta = Array.from(document.querySelectorAll('meta[name],meta[property]')).map(
                    m => (m.name || m.getAttribute('property')) + '=' + m.content.substring(0, 80)
                );
                return {
                    headings, links, buttons, inputs, images, forms, meta,
                    totalNodes: document.querySelectorAll('*').length,
                    lang: document.documentElement.lang || 'not set',
                };
            }""")
            data.dom_snapshot = _format_dom_fallback(dom_info)
        except Exception as e:
            log.warning("DOM fallback failed: %s", e)


async def collect_page_source(page, data: AuditData) -> None:
    """Store the rendered HTML.

    ``page.content()`` is the *post-JavaScript* DOM, not the served HTML —
    which is what makes it useful for finding client-rendered secrets, and why
    it is stored alongside the network log rather than instead of it.
    """
    try:
        data.page_source = await page.content()
    except Exception as e:
        log.warning("Page source capture failed: %s", e)


def attach_network_to_data(data: AuditData, capture: NetworkCapture) -> None:
    """Copy what the listeners collected onto the audit record."""
    data.network_requests = capture.requests
    data.response_headers = capture.response_headers
    data.cookies = capture.cookies
    data.console_logs = capture.console_logs


async def collect_performance(page, data: AuditData) -> None:
    """Fill ``lighthouse_report`` from the browser's own Performance API.

    Not a Lighthouse run: this reads ``performance.getEntriesByType`` for the
    paint timings, layout shift, DOM size and TTFB, and scores them against the
    same thresholds Lighthouse uses. It costs one ``page.evaluate`` instead of a
    separate Node process, and it is why ``raw_metrics`` is kept in the report —
    the scores are derived, the metrics are the evidence.
    """
    try:
        perf_data = await page.evaluate("""() => {
            const nav = performance.getEntriesByType('navigation')[0];
            const paint = performance.getEntriesByType('paint');
            const lcpEntry = performance.getEntriesByType('largest-contentful-paint');
            const clsValue = (performance.getEntriesByType('layout-shift') || []).reduce(
                (sum, e) => sum + (e.value || 0), 0
            );

            let lcp = lcpEntry.length > 0 ? lcpEntry[lcpEntry.length - 1].startTime : null;
            let fcp = paint.find(p => p.name === 'first-contentful-paint');
            let fp = paint.find(p => p.name === 'first-paint');

            return {
                fcp: fcp ? Math.round(fcp.startTime) : null,
                fp: fp ? Math.round(fp.startTime) : null,
                lcp: lcp ? Math.round(lcp) : null,
                cls: clsValue ? Math.round(clsValue * 10000) / 10000 : null,
                dom_content_loaded: nav ? Math.round(nav.domContentLoadedEventEnd) : null,
                load_complete: nav ? Math.round(nav.loadEventEnd) : null,
                ttfb: nav ? Math.round(nav.responseStart - nav.requestStart) : null,
                dom_nodes: document.querySelectorAll('*').length,
            }
        }""")

        data.lighthouse_report = {
            "source": "Performance API (Lighthouse not available — install lighthouse for full audits)",
            "categories": {
                "performance": {"score": _estimate_perf_score(perf_data), "title": "Performance"},
            },
            "audits": {
                "first-contentful-paint": {
                    "title": "First Contentful Paint",
                    "score": _score_metric(perf_data.get("fcp"), [1800, 3000]),
                    "displayValue": f"{perf_data.get('fcp', 'N/A')}ms" if perf_data.get("fcp") else "N/A",
                },
                "largest-contentful-paint": {
                    "title": "Largest Contentful Paint",
                    "score": _score_metric(perf_data.get("lcp"), [2500, 4000]),
                    "displayValue": f"{perf_data.get('lcp', 'N/A')}ms" if perf_data.get("lcp") else "N/A",
                },
                "cumulative-layout-shift": {
                    "title": "Cumulative Layout Shift",
                    "score": _score_metric(perf_data.get("cls"), [0.1, 0.25], lower_is_better=True),
                    "displayValue": str(perf_data.get("cls", "N/A")),
                },
                "dom-size": {
                    "title": "DOM Size",
                    "score": _score_metric(perf_data.get("dom_nodes"), [800, 1500]),
                    "displayValue": f"{perf_data.get('dom_nodes', 'N/A')} nodes",
                },
                "server-response-time": {
                    "title": "Server Response Time (TTFB)",
                    "score": _score_metric(perf_data.get("ttfb"), [600, 1000]),
                    "displayValue": f"{perf_data.get('ttfb', 'N/A')}ms" if perf_data.get("ttfb") else "N/A",
                },
            },
            "raw_metrics": perf_data,
        }
    except Exception as e:
        log.warning("Performance metrics collection failed: %s", e)


def log_collection(req: "AuditCollectRequest", data: AuditData,
                   capture: NetworkCapture) -> None:
    """Write the audit-trail entry for this collection.

    Counts, not payloads: the trail answers "what did we look at", while the
    evidence bundle answers "what did we find".
    """
    try:
        audit = get_audit_logger()
        audit.log(ActionCategory.RESEARCH, "audit_collect", details={
            "url": req.url, "load_ms": data.load_time_ms,
            "requests": len(capture.requests),
                "console": len(capture.console_logs),
        })
    except Exception:
        log.debug("audit: ignored failure", exc_info=True)


# ── Scoring and formatting helpers ───────────────────────────────────────
# These moved with the phases that call them; they are pure functions and are
# the easiest part of this module to test without a browser.

def _estimate_perf_score(metrics: dict) -> float:
    """Estimate a 0-1 performance score from raw metrics."""
    if not metrics:
        return 0.0
    scores = []
    if metrics.get("lcp"):
        scores.append(_score_metric(metrics["lcp"], [2500, 4000]))
    if metrics.get("fcp"):
        scores.append(_score_metric(metrics["fcp"], [1800, 3000]))
    if metrics.get("cls") is not None:
        scores.append(_score_metric(metrics["cls"], [0.1, 0.25], lower_is_better=True))
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def _score_metric(value, thresholds: list, lower_is_better: bool = False) -> float:
    """Score a metric 0-1 based on good/needs-improvement thresholds."""
    if value is None:
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    good, poor = thresholds[0], thresholds[1]
    if lower_is_better:
        if v <= good:
            return 1.0
        if v >= poor:
            return 0.0
        return round(1 - (v - good) / (poor - good), 2)
    else:
        if v <= good:
            return 1.0
        if v >= poor:
            return 0.0
        return round(1 - (v - good) / (poor - good), 2)


def _format_accessibility_tree(node, depth: int = 0) -> str:
    """Convert Playwright accessibility snapshot to readable text."""
    lines = []
    indent = "  " * depth
    role = node.get("role", "unknown")
    name = node.get("name", "")
    value = node.get("value", "")
    desc = f"{role}"
    if name:
        desc += f" '{name[:60]}'"
    if value:
        desc += f" = {value}"
    lines.append(f"{indent}{desc}")

    children = node.get("children", [])
    for child in children:
        if isinstance(child, dict):
            lines.append(_format_accessibility_tree(child, depth + 1))
    return "\n".join(lines)


def _format_dom_fallback(dom_info: dict) -> str:
    """Format basic DOM structure when accessibility tree is unavailable."""
    lines = [f"DOM nodes: {dom_info.get('totalNodes', '?')}, lang={dom_info.get('lang', '?')}\n"]

    sections = [
        ("HEADINGS", "headings"),
        ("LINKS", "links"),
        ("BUTTONS", "buttons"),
        ("INPUTS", "inputs"),
        ("IMAGES", "images"),
        ("FORMS", "forms"),
        ("META TAGS", "meta"),
    ]
    for label, key in sections:
        items = dom_info.get(key, [])
        if items:
            lines.append(f"--- {label} ({len(items)}) ---")
            for item in items[:15]:
                lines.append(f"  {item}")

    return "\n".join(lines)


    # ---------------------------------------------------------------------------
    # POST /audit/collect
    # ---------------------------------------------------------------------------
