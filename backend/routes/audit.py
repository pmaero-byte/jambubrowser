"""Audit Routes — AI employee orchestration for webapp analysis.

POST /audit/collect  — Playwright CDP data collection pipeline
POST /audit/run      — full audit: all 6 employees, SSE streaming
POST /audit/quick    — quick scan: 3 employees (Security, Performance, UX)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator

from backend.core.audit import get_audit_logger, ActionCategory
from backend.core.security import is_safe_url
from backend.employees import (
    ALL_EMPLOYEES,
    QUICK_SCAN_EMPLOYEES,
    AuditData,
    Finding,
)

log = logging.getLogger("jambu.audit")
router = APIRouter(prefix="/audit", tags=["audit"])


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class AuditCollectRequest(BaseModel):
    url: str
    width: int = 1440
    height: int = 900
    timeout_ms: int = 30000
    capture_screenshot: bool = True
    capture_fullpage: bool = False

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


class AuditRunRequest(BaseModel):
    url: str
    mode: str = "full"  # "full" or "quick"
    timeout_ms: int = 60000
    provider: Optional[str] = None  # LLM provider override

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("Invalid or blocked URL")
        return v


# ---------------------------------------------------------------------------
# Playwright Data Collection
# ---------------------------------------------------------------------------

_playwright = None
_playwright_lock = asyncio.Lock()


async def _get_playwright():
    global _playwright
    if _playwright is not None:
        return _playwright
    async with _playwright_lock:
        if _playwright is not None:
            return _playwright
        try:
            from playwright.async_api import async_playwright
            _playwright = await async_playwright().start()
            return _playwright
        except ImportError:
            raise HTTPException(
                status_code=501,
                detail="Playwright not installed. Run: pip install playwright && playwright install chromium",
            )


async def collect_page_data(req: AuditCollectRequest) -> AuditData:
    """Navigate to URL and collect full-spectrum data via Playwright/CDP."""
    pw = await _get_playwright()
    data = AuditData(url=req.url, collected_at=datetime.now(timezone.utc).isoformat())

    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--disable-dev-shm-usage",
        ],
    )

    context = await browser.new_context(
        viewport={"width": req.width, "height": req.height},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    )

    page = await context.new_page()
    start_time = time.time()

    # ── Collectors (attach before navigation) ──────────────────────────

    network_requests: list[dict] = []
    response_headers: dict[str, str] = {}
    cookies: list[dict] = []

    async def on_request(request):
        pass  # tracked on response

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
            pass

        network_requests.append({
            "url": req.url,
            "method": req.method,
            "status": response.status,
            "status_text": response.status_text,
            "resource_type": req.resource_type,
            "transfer_size": response.headers.get("content-length", 0),
            "timing": timing,
        })

    async def on_main_response(response):
        nonlocal response_headers
        if response.request.resource_type == "document":
            response_headers = dict(response.headers)
            try:
                page_cookies = await context.cookies()
                cookies.extend(page_cookies)
            except Exception:
                pass

    console_logs: list[dict] = []

    async def on_console(msg):
        console_logs.append({
            "level": msg.type,
            "text": msg.text,
            "location": f"{msg.location.get('url','')}:{msg.location.get('lineNumber','')}" if msg.location else "",
        })

    page.on("response", on_response)
    page.on("response", on_main_response)
    page.on("console", on_console)

    # ── Navigate ───────────────────────────────────────────────────────

    try:
        main_response = await page.goto(
            req.url,
            wait_until="networkidle",
            timeout=req.timeout_ms,
        )
        if main_response:
            response_headers.update(dict(main_response.headers))
            data.title = await page.title()

        # Small extra wait for late-loading resources
        await asyncio.sleep(1.0)
    except Exception as e:
        log.warning("Navigation to %s had issues: %s", req.url, e)
        try:
            data.title = await page.title()
        except Exception:
            pass

    data.load_time_ms = (time.time() - start_time) * 1000
    data.viewport_width = req.width
    data.viewport_height = req.height

    # ── Screenshots ────────────────────────────────────────────────────

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

    # ── DOM / Accessibility Snapshot ───────────────────────────────────

    try:
        snapshot = await page.accessibility.snapshot()
        if snapshot:
            data.dom_snapshot = _format_accessibility_tree(snapshot)
    except Exception as e:
        log.warning("Accessibility snapshot failed: %s", e)

    # ── Page Source ────────────────────────────────────────────────────

    try:
        data.page_source = await page.content()
    except Exception as e:
        log.warning("Page source capture failed: %s", e)

    # ── Network + Cookies ──────────────────────────────────────────────

    data.network_requests = network_requests
    data.response_headers = response_headers
    data.cookies = cookies
    data.console_logs = console_logs

    # ── Lighthouse-lite (Performance API metrics) ──────────────────────

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

    # ── Cleanup ────────────────────────────────────────────────────────

    page.remove_listener("response", on_response)
    page.remove_listener("response", on_main_response)
    page.remove_listener("console", on_console)
    await context.close()
    await browser.close()

    # Log to audit trail
    try:
        audit = get_audit_logger()
        audit.log(ActionCategory.RESEARCH, "audit_collect", details={
            "url": req.url, "load_ms": data.load_time_ms,
            "requests": len(network_requests), "console": len(console_logs),
        })
    except Exception:
        pass

    return data


# ── Helpers ───────────────────────────────────────────────────────────


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


# ---------------------------------------------------------------------------
# POST /audit/collect
# ---------------------------------------------------------------------------


@router.post("/collect")
async def audit_collect(req: AuditCollectRequest):
    """Collect all page data: screenshot, DOM, network, console, source, metrics."""
    try:
        data = await collect_page_data(req)
        return {
            "url": data.url,
            "title": data.title,
            "load_time_ms": data.load_time_ms,
            "viewport": f"{data.viewport_width}x{data.viewport_height}",
            "screenshot": bool(data.screenshot_base64),
            "fullpage_screenshot": bool(data.fullpage_screenshot_base64),
            "dom_snapshot_chars": len(data.dom_snapshot or ""),
            "page_source_chars": len(data.page_source or ""),
            "network_requests": len(data.network_requests),
            "console_logs": len(data.console_logs),
            "cookies": len(data.cookies),
            "lighthouse": data.lighthouse_report is not None,
            "data": data,  # Full data for follow-up /audit/run
        }
    except Exception as e:
        log.exception("audit/collect failed for %s", req.url)
        raise HTTPException(status_code=500, detail=f"Data collection failed: {e}")


# ---------------------------------------------------------------------------
# POST /audit/run  +  /audit/quick
# ---------------------------------------------------------------------------


@router.post("/run")
async def audit_run(req: AuditRunRequest):
    """Full audit: collect data, dispatch all 6 employees in parallel, stream findings via SSE."""
    employees = ALL_EMPLOYEES if req.mode == "full" else QUICK_SCAN_EMPLOYEES

    async def event_stream():
        # Phase 1: Collect
        yield _sse("status", {"phase": "collecting", "url": req.url})
        try:
            collect_req = AuditCollectRequest(
                url=req.url,
                capture_screenshot=True,
                capture_fullpage=False,
                timeout_ms=req.timeout_ms // 2,
            )
            data = await collect_page_data(collect_req)
            yield _sse("status", {
                "phase": "collected",
                "load_ms": data.load_time_ms,
                "requests": len(data.network_requests),
                "console": len(data.console_logs),
                "summary": data.summary(),
            })
        except Exception as e:
            yield _sse("error", {"phase": "collect", "error": str(e)})
            return

        # Phase 2: Dispatch employees in parallel
        yield _sse("status", {"phase": "analyzing", "employees": [e.name for e in employees]})

        async def run_employee(emp_cls):
            emp = emp_cls()
            start = time.time()
            try:
                findings = await emp.analyze(data)
                elapsed = round((time.time() - start) * 1000)
                return emp.name, emp.emoji, findings, elapsed, None
            except Exception as e:
                elapsed = round((time.time() - start) * 1000)
                log.exception("%s failed", emp_cls.name)
                return emp_cls.name, emp_cls.emoji, [], elapsed, str(e)

        tasks = [asyncio.create_task(run_employee(e)) for e in employees]

        all_findings: list[Finding] = []
        for coro in asyncio.as_completed(tasks):
            name, emoji, findings, elapsed, error = await coro
            if error:
                yield _sse("employee_error", {
                    "employee": name, "emoji": emoji,
                    "error": error, "elapsed_ms": elapsed,
                })
            else:
                all_findings.extend(findings)
                yield _sse("employee_done", {
                    "employee": name, "emoji": emoji,
                    "findings_count": len(findings),
                    "elapsed_ms": elapsed,
                    "findings": [f.to_dict() for f in findings],
                })

        # Phase 3: Summary
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in all_findings:
            by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1

        yield _sse("done", {
            "total_findings": len(all_findings),
            "by_severity": by_severity,
            "url": req.url,
            "mode": req.mode,
        })

        # Audit log
        try:
            audit = get_audit_logger()
            audit.log(ActionCategory.RESEARCH, "audit_complete", details={
                "url": req.url, "mode": req.mode,
                "findings": len(all_findings), "by_severity": by_severity,
            })
        except Exception:
            pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/quick")
async def audit_quick(req: AuditRunRequest):
    """Quick scan: 3 employees (Security, Performance, UX), fast turnaround."""
    req.mode = "quick"
    return await audit_run(req)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
