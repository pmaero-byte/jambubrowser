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
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, validator

from backend.core.audit import get_audit_logger, ActionCategory
from backend.core.security import is_safe_url
from backend.employees import (
    ALL_EMPLOYEES,
    QUICK_SCAN_EMPLOYEES,
    AuditData,
    Finding,
)
from backend.employees.export import (
    content_fingerprint,
    findings_to_canonical_json,
    findings_to_html,
    findings_to_markdown,
    findings_to_sarif,
    sarif_to_json,
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
            "transfer_size": int(response.headers.get("content-length", 0) or 0),
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
        }
    except Exception as e:
        log.exception("audit/collect failed for %s", req.url)
        raise HTTPException(status_code=500, detail=f"Data collection failed: {e}")


# ---------------------------------------------------------------------------
# POST /audit/run  +  /audit/quick
# ---------------------------------------------------------------------------


@router.post("/run")
async def audit_run(req: AuditRunRequest):
    """Full audit: collect data, extract product context, dispatch employees, deduplicate."""
    return _audit_stream_response(req)


@router.post("/quick")
async def audit_quick(req: AuditRunRequest):
    """Quick scan: 3 employees (Security, Performance, UX).

    Same pipeline as /audit/run with the mode forced to 'quick' — the
    frontend's quick-scan button calls this endpoint directly.
    """
    req.mode = "quick"
    return _audit_stream_response(req)


def _audit_stream_response(req: AuditRunRequest):
    """SSE wrapper: render the shared audit pipeline as text/event-stream."""

    async def event_stream():
        async for event, data in _audit_event_stream(req):
            yield _sse(event, data)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _audit_event_stream(req: AuditRunRequest, on_collected=None):
    """Run the audit pipeline, yielding ``(event_name, data)`` tuples.

    Shared by the SSE endpoints and the audit-monitor scheduler so that
    dedupe, dismissal filtering, and history persistence can never drift
    between interactive and scheduled runs. The final ``done`` event
    carries the active findings (post-dismissal).

    ``on_collected`` is an optional callback receiving the raw
    :class:`AuditData` right after collection — audit monitors use it to
    grab the screenshot for visual regression diffing without bloating
    the SSE payload.
    """
    employees = ALL_EMPLOYEES if req.mode == "full" else QUICK_SCAN_EMPLOYEES

    # Phase 1: Collect
    yield ("status", {"phase": "collecting", "url": req.url})
    try:
        collect_req = AuditCollectRequest(
            url=req.url,
            capture_screenshot=True,
            capture_fullpage=False,
            timeout_ms=req.timeout_ms // 2,
        )
        data = await collect_page_data(collect_req)
        if on_collected:
            try:
                on_collected(data)
            except Exception:
                log.warning("on_collected callback failed", exc_info=True)
        yield ("status", {
            "phase": "collected",
            "load_ms": data.load_time_ms,
            "requests": len(data.network_requests),
            "console": len(data.console_logs),
            "summary": data.summary(),
        })
    except Exception as e:
        yield ("error", {"phase": "collect", "error": str(e)})
        return

    # Phase 1.5: Extract product context
    yield ("status", {"phase": "understanding_product"})
    try:
        from backend.employees.product_context import ProductContextExtractor
        context_extractor = ProductContextExtractor()
        product_context = await context_extractor.extract_context(data)
        yield ("product_context", {
            "what_it_does": product_context.what_it_does,
            "target_audience": product_context.target_audience,
            "value_proposition": product_context.value_proposition,
            "key_features": product_context.key_features,
            "tech_stack": product_context.tech_stack,
            "business_model": product_context.business_model,
        })
    except Exception as e:
        log.warning("Product context extraction failed: %s", e)
        product_context = None

    # Phase 2: Dispatch employees in parallel
    yield ("status", {"phase": "analyzing", "employees": [e.name for e in employees]})

    context_prompt = product_context.to_prompt_context() if product_context else ""

    async def run_employee(emp_cls):
        emp = emp_cls()
        start = time.time()
        try:
            if context_prompt and hasattr(emp, 'system_prompt'):
                emp.system_prompt = context_prompt + "\n\n" + emp.system_prompt
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
            yield ("employee_error", {
                "employee": name, "emoji": emoji,
                "error": error, "elapsed_ms": elapsed,
            })
        else:
            all_findings.extend(findings)
            yield ("employee_done", {
                "employee": name, "emoji": emoji,
                "findings_count": len(findings),
                "elapsed_ms": elapsed,
                "findings": [f.to_dict() for f in findings],
            })

    # Phase 3: Deduplicate and enhance findings
    from backend.core.findings import deduplicate_findings, group_findings

    enhanced_findings = deduplicate_findings(all_findings)

    # Phase 3.5: apply stored dismissals (false-positives) scoped to
    # this URL, so the live response, history, and exports all agree
    # on which findings are active.
    active_findings, suppressed_findings = _split_dismissed_findings(
        enhanced_findings, req.url
    )
    fix_groups = group_findings(active_findings)

    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in active_findings:
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1

    # Persist before announcing completion so the done event can carry the
    # saved audit id — the UI's Report / Share / Export actions need it.
    audit_id = _save_audit_history(
        req.url, data.title, req.mode, active_findings, by_severity,
    )

    yield ("done", {
        "audit_id": audit_id,
        "total_findings": len(active_findings),
        "dismissed_count": len(suppressed_findings),
        "dismissed": suppressed_findings,
        "by_severity": by_severity,
        "url": req.url,
        "mode": req.mode,
        "product_context": {
            "what_it_does": product_context.what_it_does if product_context else None,
            "target_audience": product_context.target_audience if product_context else None,
            "value_proposition": product_context.value_proposition if product_context else None,
        },
        "fix_groups": [
            {
                "id": fg.id,
                "title": fg.title,
                "description": fg.description,
                "finding_count": len(fg.findings),
                "total_impact": fg.total_impact,
                "fix_effort": fg.fix_effort,
                "code_fix": fg.code_fix,
            }
            for fg in fix_groups
        ],
        "findings": [
            {
                **f.to_dict(),
                "business_impact": {
                    "user_impact": f.business_impact.user_impact,
                    "revenue_impact": f.business_impact.revenue_impact,
                    "fix_effort": f.business_impact.fix_effort,
                    "priority_score": f.business_impact.priority_score,
                    "reasoning": f.business_impact.reasoning,
                } if f.business_impact else None,
                "code_fix": f.code_fix,
                "fix_group": f.fix_group,
            }
            for f in active_findings
        ],
    })

    # Audit log
    try:
        audit = get_audit_logger()
        audit.log(ActionCategory.RESEARCH, "audit_complete", details={
            "url": req.url, "mode": req.mode,
            "findings": len(active_findings),
            "dismissed": len(suppressed_findings),
            "by_severity": by_severity,
        })
    except Exception:
        pass

    # Record API key usage if authenticated
    if hasattr(req, '_api_key') and req._api_key:
        try:
            from backend.core.api_keys import record_audit_usage
            record_audit_usage(
                req._api_key.id, req.mode, req.url,
                len(active_findings), data.load_time_ms,
            )
        except Exception:
            pass


def _save_audit_history(
    url: str, title: str, mode: str, findings: list[Finding], by_severity: dict,
) -> Optional[int]:
    """Persist a completed audit; returns the new row id (None on failure).

    Dismissed findings are deliberately not passed in: history and exports
    reflect the triaged state, and undismiss + re-audit is the path back.
    """
    try:
        from backend.core.database import get_db
        findings_json = json.dumps([f.to_dict() for f in findings], default=str)
        with get_db() as conn:
            cur = conn.execute("""
                INSERT INTO audit_history (url, title, mode, total_findings,
                    critical_count, high_count, medium_count, low_count, info_count,
                    findings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url, title, mode, len(findings),
                by_severity.get("critical", 0), by_severity.get("high", 0),
                by_severity.get("medium", 0), by_severity.get("low", 0),
                by_severity.get("info", 0), findings_json,
            ))
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        log.warning("Failed to save audit history: %s", e)
        return None


@router.get("/history")
async def audit_history(limit: int = 20, offset: int = 0):
    from backend.core.database import get_db
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, url, title, mode, total_findings,
                   critical_count, high_count, medium_count, low_count, info_count,
                   created_at, share_token
            FROM audit_history
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

    audits = []
    for r in rows:
        record = dict(r)
        record["created_at"] = _created_at_epoch(record.get("created_at"))
        audits.append(record)
    return {
        "audits": audits,
        "total": len(audits),
    }


def _created_at_epoch(value) -> Optional[float]:
    """Normalise ``audit_history.created_at`` to epoch seconds.

    Rows written before 2026-09 used SQLite ``julianday('now')`` (a Julian
    Day number like 2460570.5); newer rows store epoch seconds. Clients
    (CLI, UI) should never have to know that — this converts JD values
    transparently so history dates don't render as 1970.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 1_000_000_000:  # Julian Day (~2.46e6); epoch seconds are ~1.7e9
        return (v - 2440587.5) * 86400.0
    return v


@router.get("/history/{audit_id}")
async def audit_history_detail(audit_id: int):
    from backend.core.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM audit_history WHERE id = ?", (audit_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Audit not found")

    result = dict(row)
    if result.get("findings_json"):
        result["findings"] = json.loads(result["findings_json"])
        del result["findings_json"]
    return result


@router.post("/history/{audit_id}/share")
async def audit_share(audit_id: int):
    import secrets
    token = secrets.token_urlsafe(16)
    from backend.core.database import get_db
    with get_db() as conn:
        result = conn.execute(
            "UPDATE audit_history SET share_token = ? WHERE id = ?",
            (token, audit_id),
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Audit not found")

    return {"share_token": token, "share_url": f"/audit/shared/{token}"}


@router.get("/shared/{token}")
async def audit_shared(token: str):
    from backend.core.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM audit_history WHERE share_token = ?", (token,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Shared audit not found or expired")

    result = dict(row)
    result["created_at"] = _created_at_epoch(result.get("created_at"))
    if result.get("findings_json"):
        result["findings"] = json.loads(result["findings_json"])
        del result["findings_json"]
    return result


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------
# These let any saved audit (or a one-shot in-memory finding list) be
# downloaded as SARIF (industry-standard static-analysis format — accepted by
# GitHub Code Scanning, GitLab Code Quality, VS Code SARIF Viewer, Azure
# DevOps), canonical JSON (lossless, for custom dashboards / pipelines), or
# Markdown (human-readable). The same envelope is reused by the frontend's
# `Export` dropdown.

def _load_findings_row(where: str, params: tuple) -> tuple[int, str, list[Finding], dict]:
    """Load one audit_history row by arbitrary predicate.

    Returns (audit_id, url, findings, summary). Raises 404 when absent.
    """
    from backend.core.database import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, url, findings_json, mode, total_findings, critical_count,"
            " high_count, medium_count, low_count, info_count, created_at"
            f" FROM audit_history WHERE {where}",
            params,
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Audit not found")
    raw = row["findings_json"] if "findings_json" in row.keys() else None
    findings_data = json.loads(raw) if raw else []
    findings = [Finding.from_dict(f) for f in findings_data]
    summary = {
        "mode": row["mode"],
        "total_findings": row["total_findings"],
        "critical": row["critical_count"],
        "high": row["high_count"],
        "medium": row["medium_count"],
        "low": row["low_count"],
        "info": row["info_count"],
        "created_at": _created_at_epoch(row["created_at"]),
    }
    return row["id"], row["url"], findings, summary


def _load_findings_for_audit(audit_id: int) -> tuple[str, list[Finding], dict]:
    """Load findings + the audit envelope for a saved audit."""
    _, url, findings, summary = _load_findings_row("id = ?", (audit_id,))
    return url, findings, summary


def _load_findings_for_token(token: str) -> tuple[str, list[Finding], dict]:
    """Load findings + envelope for a share token (404 when unknown)."""
    _, url, findings, summary = _load_findings_row("share_token = ?", (token,))
    return url, findings, summary


@router.get("/report/{audit_id}")
async def audit_html_report(audit_id: int):
    """Self-contained HTML report for a saved audit (print-friendly / PDF)."""
    audited_url, findings, summary = _load_findings_for_audit(audit_id)
    html = findings_to_html(
        findings,
        audited_url=audited_url,
        summary={**summary, "engine": "Jambubrowser"},
    )
    return HTMLResponse(html)


@router.get("/shared/{token}/report")
async def audit_html_report_shared(token: str):
    """HTML report for a shared audit link (same rendering as /report/{id})."""
    audited_url, findings, summary = _load_findings_for_token(token)
    html = findings_to_html(
        findings,
        audited_url=audited_url,
        summary={**summary, "shared": "yes"},
    )
    return HTMLResponse(html)


@router.get("/export/sarif")
async def audit_export_sarif(audit_id: int):
    """Download a saved audit as SARIF 2.1.0 JSON.

    Wire format:
    - Content-Type: application/sarif+json (the SARIF 2.1.0 standard).
    - Filename: jambu-audit-{id}.sarif

    Tested consumers: GitHub Code Scanning via `github/codeql-action/upload-sarif`,
    GitLab Code Quality, VS Code `microsoft.sarif-viewer`, Azure DevOps
    "Publish Code Analysis Results" v2.
    """
    audited_url, findings, summary = _load_findings_for_audit(audit_id)
    sarif = findings_to_sarif(
        findings,
        audited_url=audited_url,
        run_id=f"audit-{audit_id}",
    )
    body = sarif_to_json(sarif)
    return StreamingResponse(
        iter([body]),
        media_type="application/sarif+json",
        headers={"Content-Disposition": f'attachment; filename="jambu-audit-{audit_id}.sarif"'},
    )


@router.get("/export/json")
async def audit_export_json(audit_id: int):
    """Download a saved audit as canonical JSON."""
    audited_url, findings, summary = _load_findings_for_audit(audit_id)
    body = findings_to_canonical_json(findings, audited_url=audited_url, summary=summary)
    payload = json.dumps(body, indent=2, sort_keys=False, default=str)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="jambu-audit-{audit_id}.json"'},
    )


@router.get("/export/markdown")
async def audit_export_markdown(audit_id: int):
    """Download a saved audit as Markdown."""
    audited_url, findings, summary = _load_findings_for_audit(audit_id)
    md = findings_to_markdown(findings, audited_url=audited_url, summary=summary)
    return StreamingResponse(
        iter([md]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="jambu-audit-{audit_id}.md"'},
    )


# ---------------------------------------------------------------------------
# "Live" export — the current in-memory audit results, before they're saved
# to history. Useful for the AuditPanel's Export menu: no need to persist
# first, the user gets the SARIF/JSON/MD immediately.
# ---------------------------------------------------------------------------

class _LiveExportRequest(BaseModel):
    url: str
    findings: list[dict]
    summary: dict | None = None

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("URL failed safety checks (SSRF blocklist)")
        return v


@router.post("/export/sarif")
async def audit_export_sarif_live(req: _LiveExportRequest):
    """SARIF for findings that haven't been persisted yet (e.g. live panel)."""
    findings = [Finding.from_dict(f) for f in (req.findings or [])]
    sarif = findings_to_sarif(findings, audited_url=req.url)
    return {"sarif": sarif}


@router.post("/export/json")
async def audit_export_json_live(req: _LiveExportRequest):
    """Canonical JSON for findings that haven't been persisted yet."""
    findings = [Finding.from_dict(f) for f in (req.findings or [])]
    return findings_to_canonical_json(
        findings, audited_url=req.url, summary=req.summary or {}
    )


@router.post("/export/markdown")
async def audit_export_markdown_live(req: _LiveExportRequest):
    """Markdown for findings that haven't been persisted yet."""
    findings = [Finding.from_dict(f) for f in (req.findings or [])]
    return {"markdown": findings_to_markdown(
        findings, audited_url=req.url, summary=req.summary or {}
    )}


# ---------------------------------------------------------------------------
# Dismiss / undismiss findings
# ---------------------------------------------------------------------------
# Users triaging 50+ findings need to mark false-positives as "not a bug".
# Dismissals are scoped per URL + per finding fingerprint (employee +
# category + severity + title), so they survive across re-audits without
# pinning to a per-audit UUID that would change every run.

class DismissRequest(BaseModel):
    url: str
    finding: dict
    reason: str | None = None
    actor: str = "default"

    @validator("url")
    def validate_url(cls, v):
        if not is_safe_url(v):
            raise ValueError("URL failed safety checks (SSRF blocklist)")
        return v


def _fingerprint(finding_dict: dict) -> str:
    """Kept as an alias so older callers/tests keep working; delegates to
    the canonical exporter hash (single source of truth)."""
    return content_fingerprint(finding_dict)


def _dismissed_fingerprints(url: str) -> set[str]:
    """All dismissed finding fingerprints for a URL (empty set on failure)."""
    from backend.core.database import get_db
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT fingerprint FROM dismissed_findings WHERE url = ?",
                (url,),
            ).fetchall()
        return {row["fingerprint"] for row in rows}
    except Exception:
        log.warning("Could not load dismissed findings for %s", url, exc_info=True)
        return set()


def _split_dismissed_findings(
    findings: list[Finding], url: str
) -> tuple[list[Finding], list[dict]]:
    """Partition findings into (active, suppressed) using stored dismissals.

    Suppressed entries carry enough metadata for the UI to show an
    "ignored (N)" section and offer an undo.
    """
    dismissed = _dismissed_fingerprints(url)
    if not dismissed:
        return findings, []
    active: list[Finding] = []
    suppressed: list[dict] = []
    for f in findings:
        fp = content_fingerprint(f)
        if fp in dismissed:
            suppressed.append({
                "fingerprint": fp,
                "title": f.title,
                "employee": f.employee,
                "category": f.category,
                "severity": f.severity.value,
            })
        else:
            active.append(f)
    return active, suppressed


@router.post("/dismiss")
async def audit_dismiss(req: DismissRequest):
    """Mark a finding as dismissed (false-positive / not-applicable).

    Idempotent: dismissing the same fingerprint + URL twice is a no-op.
    """
    from backend.core.database import get_db
    fp = _fingerprint(req.finding)
    employee = str(req.finding.get("employee", ""))
    category = str(req.finding.get("category", "")) or "uncategorised"
    severity = str(req.finding.get("severity", "medium"))
    title = str(req.finding.get("title", ""))

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO dismissed_findings
                (fingerprint, url, employee, category, severity, title, reason, dismissed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint, url) DO UPDATE SET
                reason = excluded.reason,
                dismissed_by = excluded.dismissed_by,
                dismissed_at = julianday('now')
            """,
            (fp, req.url, employee, category, severity, title, req.reason, req.actor),
        )

    return {
        "status": "dismissed",
        "fingerprint": fp,
        "url": req.url,
    }


@router.delete("/dismiss")
async def audit_undismiss(url: str, fingerprint: str):
    """Un-dismiss a previously dismissed finding."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="URL failed safety checks")
    from backend.core.database import get_db
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM dismissed_findings WHERE fingerprint = ? AND url = ?",
            (fingerprint, url),
        )
        deleted = cur.rowcount
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Dismissal not found")
    return {"status": "undismissed", "fingerprint": fingerprint, "url": url}


@router.get("/dismissed")
async def audit_dismissed_list(url: str):
    """List all dismissed fingerprints for a URL — for the UI to filter them out."""
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="URL failed safety checks")
    from backend.core.database import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT fingerprint, employee, category, severity, title, reason,"
            " dismissed_by, dismissed_at FROM dismissed_findings WHERE url = ?",
            (url,),
        ).fetchall()
    return {
        "url": url,
        "dismissed": [dict(r) for r in rows],
        "count": len(rows),
    }

