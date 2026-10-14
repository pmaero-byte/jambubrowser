"""
Dedicated Desktop App Integration Endpoints (YaadhumAI & native macOS companion).

Exposes streamlined, low-latency, single-round-trip endpoints for desktop apps:
- GET  /browser/desktop/status   — aggregated multi-daemon health (Jambu, :8095 Memory Bridge, :8085/:8090 LLMs)
- POST /browser/desktop/inspect  — navigate, extract AX tree, clean markdown, capture JPEG frame, optional Memory Bridge sync
- POST /browser/desktop/act      — deterministic action (click/type by ref or coords) with instant re-render
- POST /browser/desktop/audit    — real PII, tracker, security & isolation audit
- POST /browser/desktop/research — deep research & Tamil Siddha citation extraction using local Qwen 27B + Memory Bridge
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.security import is_safe_file_url, is_safe_url, local_workspace_roots
from backend.modules.browser_agent import (
    BrowserAgentSession,
    SessionRefused,
    get_browser_agent_service,
)
from backend.modules.greenwich_bridge import (
    ingest_memory,
    memory_bridge_url,
    memory_status,
)

log = logging.getLogger("jambu.browser_desktop")

router = APIRouter(prefix="/browser/desktop", tags=["browser-desktop"])

# Secondary router for convenience alias (/browser/yaadhum/*)
yaadhum_router = APIRouter(prefix="/browser/yaadhum", tags=["browser-yaadhum"])


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class DesktopInspectRequest(BaseModel):
    url: str
    session_id: Optional[str] = None
    auto_sync_memory: bool = False
    capture_frame: bool = True
    frame_quality: int = 75
    wait_ms: int = 400


class DesktopActRequest(BaseModel):
    session_id: str
    action: str = "click"  # click | type | press | scroll | move
    ref: Optional[int] = None
    text: Optional[str] = ""
    press_enter: bool = False
    x: Optional[float] = None
    y: Optional[float] = None
    delta_x: Optional[float] = 0.0
    delta_y: Optional[float] = 0.0
    frame_quality: int = 75


class DesktopAuditRequest(BaseModel):
    session_id: Optional[str] = None
    url: Optional[str] = None
    sync_memory: bool = False


class DesktopResearchRequest(BaseModel):
    session_id: Optional[str] = None
    url: Optional[str] = None
    task: str = "deep_research"  # deep_research | siddha_citations | custom
    prompt: Optional[str] = None
    max_tokens: int = 600
    sync_memory: bool = True


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "about:blank"
    if url.startswith("file://") or url.startswith("about:"):
        return url
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        # Bare domain or localhost:port
        return f"https://{url}"
    return url


def _clean_markdown_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    lines = []
    for line in raw_text.splitlines():
        trimmed = line.strip()
        if trimmed:
            lines.append(trimmed)
    return "\n\n".join(lines)


async def _probe_url(url: str, timeout: float = 1.5) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=timeout)
            return r.status_code in (200, 404)
    except Exception:
        return False


async def _ensure_desktop_session(url: str, session_id: Optional[str] = None) -> BrowserAgentSession:
    svc = get_browser_agent_service()
    parsed = urlparse(url)
    host = (parsed.hostname or "127.0.0.1").lower()
    domains = [d for d in ("127.0.0.1", "localhost", host) if d]

    session = None
    if session_id:
        try:
            session = svc.get(session_id)
        except SessionRefused:
            pass

    if session is None:
        session = await svc.open(
            allow_domains=domains,
            require_approval=False,
            allow_private=True,
            scrub_pii=False,
        )
    else:
        if host and host not in session.allow_domains:
            session.allow_domains.append(host)
        if session.network_policy and host:
            if host not in session.network_policy.allow_domains:
                session.network_policy.allow_domains.append(host)
    return session


# ---------------------------------------------------------------------------
# 1. Aggregated Multi-Daemon Health Status
# ---------------------------------------------------------------------------

@router.get("/status")
@yaadhum_router.get("/status")
async def desktop_status() -> dict[str, Any]:
    """Complete multi-daemon status for YaadhumAI desktop application."""
    from backend import __version__
    from backend.llm.config import get_config

    cfg = get_config()
    svc = get_browser_agent_service()

    # Parallel probes for Memory Bridge (:8095) and LLM daemons (:8085, :8090)
    mem_fut = memory_status(timeout=2.0)
    qwen_fut = _probe_url(f"{cfg.greenwich_base_url}/models", timeout=1.5)
    daemon_fut = _probe_url(f"{cfg.greenwich_extended_url}/models", timeout=1.5)

    mem_res, qwen_up, daemon_up = await asyncio.gather(mem_fut, qwen_fut, daemon_fut)

    # Local workspace configuration
    local_allowed = os.environ.get("ALLOW_LOCAL_WORKSPACES", "").lower() in ("1", "true", "yes", "on")
    roots = local_workspace_roots()

    return {
        "ok": True,
        "engine": {
            "name": "Jambubrowser Engine",
            "version": __version__,
            "port": 8001,
            "active_sessions": len(svc._sessions),
        },
        "local_workspaces": {
            "allowed": local_allowed,
            "workspace_roots": roots,
        },
        "memory_bridge": {
            "url": memory_bridge_url(),
            "ok": mem_res.get("ok", False),
            "details": mem_res.get("bridge") or mem_res.get("error"),
        },
        "greenwich_llm": {
            "primary_url": cfg.greenwich_base_url,
            "primary_online": qwen_up,
            "extended_url": cfg.greenwich_extended_url,
            "extended_online": daemon_up,
            "default_model": cfg.greenwich_model,
        },
    }


# ---------------------------------------------------------------------------
# 2. Inspect: Navigate, Extract AX Tree, Markdown & Frame in One Call
# ---------------------------------------------------------------------------

@router.post("/inspect")
@yaadhum_router.post("/inspect")
async def desktop_inspect(req: DesktopInspectRequest) -> dict[str, Any]:
    """Single round-trip page inspector for desktop clients.

    Creates/reuses a session, navigates, waits for DOM settle, and returns:
    - active session_id
    - accessibility tree text (with @ref tags)
    - cleaned markdown text
    - JPEG viewport frame
    - optional auto-sync to Yaadhum Memory Bridge
    """
    target = _normalize_url(req.url)
    t0 = time.time()

    # Pre-flight safety check
    parsed = urlparse(target)
    if parsed.scheme.lower() == "file":
        if not is_safe_file_url(target):
            raise HTTPException(
                status_code=403,
                detail=f"file:// URL not allowed or outside workspace roots: {target}",
            )
    elif not is_safe_url(target, allow_private=False):
        raise HTTPException(
            status_code=403,
            detail=f"Navigation blocked by security policy: {target}",
        )

    session = await _ensure_desktop_session(target, session_id=req.session_id)

    # Navigate
    try:
        await session.navigate(target)
    except SessionRefused as ref:
        raise HTTPException(status_code=403, detail={"reason": ref.reason, "message": ref.detail})

    if req.wait_ms > 0:
        await asyncio.sleep(min(req.wait_ms / 1000.0, 3.0))

    # Snapshot & AX Tree
    state = await session.snapshot()
    focused = await session.focused_ref()
    tree = session.ax_tree_text(state, focused_ref=focused)
    raw_text = state.get("text") or ""
    clean_text = _clean_markdown_text(raw_text)

    # Frame capture
    frame_b64 = ""
    if req.capture_frame:
        captured = await session.capture_frame(quality=req.frame_quality)
        if captured:
            frame_b64 = captured

    # Auto sync to memory bridge if requested
    mem_result: Optional[dict] = None
    if req.auto_sync_memory:
        mem_result = await session.sync_to_greenwich(
            title=state.get("title") or "Desktop Snapshot",
            clean_markdown=clean_text or tree,
            url=state.get("url") or target,
            citations=[target],
        )

    dur_ms = int((time.time() - t0) * 1000)

    return {
        "ok": True,
        "session_id": session.id,
        "url": state.get("url") or target,
        "title": state.get("title") or "Untitled",
        "ax_tree": tree,
        "ax_count": len(state.get("elements") or []),
        "focused_ref": focused,
        "text": clean_text,
        "frame_base64": frame_b64,
        "latency_ms": dur_ms,
        "memory_synced": bool(mem_result and mem_result.get("ok")),
        "memory_details": mem_result,
    }


# ---------------------------------------------------------------------------
# 3. Act: Interactive Click / Type with Automatic Immediate Re-render
# ---------------------------------------------------------------------------

@router.post("/act")
@yaadhum_router.post("/act")
async def desktop_act(req: DesktopActRequest) -> dict[str, Any]:
    """Execute action by AX ref or coordinate, returning the updated state immediately."""
    svc = get_browser_agent_service()
    try:
        session = svc.get(req.session_id)
    except SessionRefused as ref:
        raise HTTPException(status_code=404, detail=f"No such session: {req.session_id}")

    # Action dispatch
    if req.ref is not None:
        try:
            await session.act_by_ax_ref(
                req.action,
                req.ref,
                text=req.text or "",
                approve=True,
                press_enter=req.press_enter,
            )
        except SessionRefused as ref:
            raise HTTPException(status_code=403, detail={"reason": ref.reason, "message": ref.detail})
    elif req.x is not None and req.y is not None:
        await session.send_input(
            req.action,
            x=req.x,
            y=req.y,
            delta_x=req.delta_x or 0.0,
            delta_y=req.delta_y or 0.0,
            text=req.text or "",
        )
    else:
        raise HTTPException(status_code=400, detail="Must specify either 'ref' or ('x', 'y') coordinates")

    # Brief settle
    await asyncio.sleep(0.3)

    # Re-capture updated state
    state = await session.snapshot()
    focused = await session.focused_ref()
    tree = session.ax_tree_text(state, focused_ref=focused)
    frame_b64 = await session.capture_frame(quality=req.frame_quality) or ""

    return {
        "ok": True,
        "session_id": session.id,
        "url": state.get("url") or "",
        "title": state.get("title") or "",
        "ax_tree": tree,
        "ax_count": len(state.get("elements") or []),
        "focused_ref": focused,
        "frame_base64": frame_b64,
    }


# ---------------------------------------------------------------------------
# 4. Audit: Concrete Safety, PII, Privacy & Isolation Scan
# ---------------------------------------------------------------------------

@router.post("/audit")
@yaadhum_router.post("/audit")
async def desktop_audit(req: DesktopAuditRequest) -> dict[str, Any]:
    """Run concrete security, privacy, and PII audit on the session or URL."""
    svc = get_browser_agent_service()
    target_url = req.url or ""
    page_text = ""
    elements = []
    title = ""

    if req.session_id:
        try:
            session = svc.get(req.session_id)
            state = await session.snapshot()
            target_url = state.get("url") or target_url
            page_text = state.get("text") or ""
            elements = state.get("elements") or []
            title = state.get("title") or ""
        except SessionRefused:
            pass

    if not page_text and target_url:
        session = await _ensure_desktop_session(target_url)
        await session.navigate(_normalize_url(target_url))
        state = await session.snapshot()
        page_text = state.get("text") or ""
        elements = state.get("elements") or []
        title = state.get("title") or ""

    findings: list[dict[str, Any]] = []

    # 1. PII Scan
    email_pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    phone_pattern = r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
    jwt_pattern = r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"

    emails = re.findall(email_pattern, page_text)
    phones = re.findall(phone_pattern, page_text)
    tokens = re.findall(jwt_pattern, page_text)

    if emails:
        findings.append({
            "category": "PII",
            "severity": "medium",
            "title": f"Exposed Email Addresses ({len(emails)})",
            "detail": f"Detected {len(emails)} email address(es) in plaintext DOM.",
        })
    if phones:
        findings.append({
            "category": "PII",
            "severity": "low",
            "title": f"Potential Phone Numbers ({len(phones)})",
            "detail": f"Detected {len(phones)} telephone number candidate(s).",
        })
    if tokens:
        findings.append({
            "category": "SECURITY",
            "severity": "critical",
            "title": "Exposed Auth Tokens / JWTs",
            "detail": "Bearer token or JWT detected rendered in visible text.",
        })

    # 2. Insecure Inputs / Forms
    password_fields = [el for el in elements if el.get("role") == "textbox" and "password" in (el.get("name") or "").lower()]
    if password_fields:
        findings.append({
            "category": "SECURITY",
            "severity": "info",
            "title": "Password Input Present",
            "detail": "Password field detected; ensure HTTPS encryption and autofill isolation.",
        })

    # 3. Protocol & Isolation Check
    parsed = urlparse(target_url)
    is_https = parsed.scheme.lower() == "https"
    is_file = parsed.scheme.lower() == "file"
    is_local = parsed.hostname in ("127.0.0.1", "localhost")

    if not is_https and not is_file and not is_local and parsed.scheme.lower() == "http":
        findings.append({
            "category": "SECURITY",
            "severity": "high",
            "title": "Insecure HTTP Protocol",
            "detail": "Connection is unencrypted HTTP. Data in transit is vulnerable to eavesdropping.",
        })

    # 4. Third-party Tracking / External Scripts
    tracking_keywords = ("analytics", "facebook.net", "googletagmanager", "hotjar", "doubleclick", "clarity.ms")
    trackers_found = []
    for el in elements:
        href = (el.get("href") or "").lower()
        for kw in tracking_keywords:
            if kw in href:
                trackers_found.append(kw)
    trackers_found = list(set(trackers_found))

    if trackers_found:
        findings.append({
            "category": "PRIVACY",
            "severity": "medium",
            "title": f"Third-Party Telemetry/Trackers ({len(trackers_found)})",
            "detail": f"Detected tracking domains: {', '.join(trackers_found)}",
        })

    # Calculate Score
    penalty = sum(
        25 if f["severity"] == "critical" else 15 if f["severity"] == "high" else 7 if f["severity"] == "medium" else 2
        for f in findings
    )
    score = max(0, min(100, 100 - penalty))

    summary = (
        f"Audit complete for {target_url or 'page'}: Score {score}/100. "
        f"{len(findings)} finding(s) identified. "
        f"{'PII detected. ' if (emails or phones or tokens) else 'No plaintext PII leaks. '}"
        f"{'HTTPS enforced. ' if is_https else 'Local workspace preview. ' if (is_file or is_local) else 'Insecure connection. '}"
    )

    mem_result: Optional[dict] = None
    if req.sync_memory:
        mem_result = await ingest_memory(
            title=f"Privacy & Security Audit: {title or target_url}",
            clean_markdown=f"# Security Audit ({score}/100)\n\nURL: {target_url}\n\n{summary}\n\n## Findings\n" +
                           "\n".join([f"- **[{f['severity'].upper()}]** {f['title']}: {f['detail']}" for f in findings]),
            url=target_url,
            citations=[target_url],
            metadata={"audit_score": score, "findings_count": len(findings)},
        )

    return {
        "ok": True,
        "url": target_url,
        "title": title,
        "score": score,
        "findings_count": len(findings),
        "findings": findings,
        "summary": summary,
        "memory_synced": bool(mem_result and mem_result.get("ok")),
    }


# ---------------------------------------------------------------------------
# 5. Research: Qwen 27B Synthesis & Tamil Siddha Citation Extraction
# ---------------------------------------------------------------------------

@router.post("/research")
@yaadhum_router.post("/research")
async def desktop_research(req: DesktopResearchRequest) -> dict[str, Any]:
    """Autonomous deep research and Tamil Siddha manuscript citation extractor.

    Uses page accessibility tree & semantic text, passes to the local Safe Qwen 27B
    model (:8085 / :8090), and automatically logs citations into the Yaadhum Memory Bridge.
    """
    from backend.llm.base import ChatMessage, Role
    from backend.llm.registry import get_registry

    svc = get_browser_agent_service()
    target_url = req.url or ""
    page_text = ""
    ax_tree = ""
    title = ""

    if req.session_id:
        try:
            session = svc.get(req.session_id)
            state = await session.snapshot()
            target_url = state.get("url") or target_url
            page_text = state.get("text") or ""
            title = state.get("title") or ""
            ax_tree = session.ax_tree_text(state)
        except SessionRefused:
            pass

    if not page_text and target_url:
        session = await _ensure_desktop_session(target_url)
        await session.navigate(_normalize_url(target_url))
        state = await session.snapshot()
        page_text = state.get("text") or ""
        title = state.get("title") or ""
        ax_tree = session.ax_tree_text(state)

    snippet = (ax_tree[:1500] if ax_tree else page_text[:1500]).strip()

    # Domain-specific prompt construction
    if req.task == "siddha_citations":
        system_instruction = (
            "You are an expert scholar of Classical Tamil Siddha literature, Agathiyar manuscripts, "
            "Gnanavettiyan, alchemy (Rasavatham), and traditional medicine (Siddha Maruthuvam)."
        )
        user_prompt = (
            f"Analyze this page content and extract any Tamil Siddha literature references, "
            f"Agathiyar / Gnanavettiyan verses, medicinal herbs (Mooligai), or alchemical concepts.\n\n"
            f"Page Title: {title}\nURL: {target_url}\n\nContent:\n{snippet}\n\n"
            f"Please structure your output with:\n"
            f"1. Identified Manuscripts / Verses (with verse numbers if found)\n"
            f"2. Core Philological & Alchemical Claims\n"
            f"3. Tamil Citations & Key Terminology"
        )
    elif req.task == "deep_research":
        system_instruction = "You are a senior academic research synthesizer providing rigorous, structured analysis."
        user_prompt = (
            f"Conduct structured deep research on this web content.\n\n"
            f"Title: {title}\nURL: {target_url}\n\nContent:\n{snippet}\n\n"
            f"Provide:\n"
            f"1. Executive Summary\n"
            f"2. Key Claims & Evidentiary Basis\n"
            f"3. Critical Verification & Citations"
        )
    else:
        system_instruction = "You are a helpful AI research assistant analyzing live web content."
        user_prompt = (
            f"{req.prompt or 'Analyze this page content.'}\n\n"
            f"Title: {title}\nURL: {target_url}\n\nContent:\n{snippet}"
        )

    # Route through LLM registry (greenwich provider preferred)
    registry = get_registry()
    provider_name = "greenwich" if registry.has("greenwich") else None

    try:
        resp = await registry.chat(
            messages=[
                ChatMessage(role=Role.SYSTEM, content=system_instruction),
                ChatMessage(role=Role.USER, content=user_prompt),
            ],
            provider=provider_name,
            max_tokens=req.max_tokens,
            temperature=0.2,
            timeout=60.0,
        )
        synthesis = resp.content
        used_model = resp.model
        used_provider = resp.provider
    except Exception as exc:
        log.warning("Local LLM invocation failed, falling back: %s", exc)
        synthesis = f"Synthesis unavailable: {exc}"
        used_model = "offline"
        used_provider = "none"

    # Sync into Yaadhum Memory Bridge
    mem_result: Optional[dict] = None
    if req.sync_memory and synthesis:
        mem_result = await ingest_memory(
            title=f"Research Synthesis: {title or target_url} ({req.task})",
            clean_markdown=f"# {req.task.replace('_', ' ').title()}\n\nURL: {target_url}\n\n{synthesis}",
            url=target_url,
            citations=[target_url],
            metadata={"task": req.task, "model": used_model, "provider": used_provider},
        )

    return {
        "ok": True,
        "task": req.task,
        "title": title,
        "url": target_url,
        "synthesis": synthesis,
        "model": used_model,
        "provider": used_provider,
        "memory_synced": bool(mem_result and mem_result.get("ok")),
        "memory_details": mem_result,
    }
