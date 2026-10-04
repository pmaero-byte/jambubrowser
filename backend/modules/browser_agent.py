"""
Browser sessions as a service — the agent loop, hardened.

Exposes the perception pattern that won the category's bake-offs
(accessibility/DOM snapshot → typed element catalog with refs → deterministic
dispatch by ref) with the safety rails browser agents need in production:

- **Domain allowlist** per session. Navigations outside it are refused; link
  clicks are pre-checked against the catalog ``href`` and post-checked after
  the action (a disallowed landing page is reverted to ``about:blank`` and
  recorded as a violation).
- **Approval gates**. Sessions can require ``approve=true`` for input
  actions, and a *risk classifier* (buy/pay/delete/send/transfer/…) always
  requires explicit approval — prompt injection cannot click "Delete
  account" through the agent.
- **PII scrubbing**. Snapshot text, element names and hrefs pass through the
  shared :class:`PIIDetector` before an agent sees them.
- **Per-step receipts**. Every step is hash-chained (MeshPay's JS-faithful
  canonical serializer); the session receipt log has a Merkle root and can be
  signed into an evidence bundle.

The page is abstracted behind a small adapter (:class:`PlaywrightPage` in
production, a scripted fake in tests), so the safety semantics are unit
tested without a browser.
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import inspect
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from urllib.parse import urljoin, urlparse

from backend.core.security import is_safe_url
from backend.modules.browser_debug import (
    A11Y_JS,
    PERF_JS,
    PERF_OBSERVER_JS,
    compact_observation,
    compile_network,
    diff_elements,
    map_url_for,
    match_network,
    NetworkPolicy,
    parse_stack_frames,
    serialize_body,
    SourceMapData,
)
from backend.decentralized.meshpay import js_dumps, merkle_root

log = logging.getLogger("jambu.browser_agent")

MAX_SESSIONS = 4
SESSION_TTL_SECONDS = 900
MAX_STEPS = 200
MAX_ELEMENTS = 200
MAX_TEXT_CHARS = 4000

# Flow runner (declarative multi-step agent testing in one tool call).
MAX_FLOW_STEPS = 100
MAX_TARGET_CANDIDATES = 5
MAX_TELEMETRY = 100
DEFAULT_STEP_TIMEOUT_MS = 5000
MAX_ASSERT_TEXT = 2000
# Compound acts: one step, one catalog read, one re-observation.
MAX_BATCH_ACTS = 25
# File uploads / downloads: bounded so a flow cannot exfiltrate or hoard disks.
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_FRAMES = 12
# Options ``snapshot(compact=…)`` forwards to the pure observation projector.
_OBSERVE_OPTIONS = frozenset({
    "query", "roles", "fields", "match", "changed_since",
    "include_hidden", "limit", "text", "max_tokens",
})
# How many times a flow may silently re-observe to rescue a stale target
# before it gives up and asks the agent for a fresh observation.
DEFAULT_REOBSERVE_BUDGET = 3

# Actions that change page state and therefore trigger an internal re-observe.
_MUTATING_ACTIONS = {
    "click", "type", "press", "select", "hover", "navigate", "reload",
    "back", "forward", "check", "uncheck", "evaluate", "upload", "download",
}

# Actions the single-primitive verb (``act``) accepts; everything else in the
# vocabulary is reachable through the flow runner / batch verb.
_ACT_ACTIONS = ("click", "type", "upload")

# Loopback / private hosts that a *local* test session is allowed to reach.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}

# Injected before flows so transitions/animations don't cause flaky reads.
DISABLE_ANIM_CSS = (
    "*,*::before,*::after{transition:none!important;animation:none!important;"
    "animation-duration:0s!important;caret-color:transparent!important}"
)

# Words that mark an action as irreversible/high-stakes regardless of session
# settings. Matched case-insensitively against element name/role/href.
RISKY_PATTERNS = (
    "buy", "purchase", "checkout", "pay ", "payment", "delete", "remove",
    "send", "transfer", "withdraw", "subscribe", "unsubscribe", "confirm",
    "order", "book", "publish", "post ", "submit", "sign up", "sign-up",
    "donate", "upgrade", "cancel plan", "close account",
)

# Catalog collector. Takes ``[start]`` so the ref numbering continues across
# frames, and walks *into* open shadow roots — a custom element's buttons are
# otherwise invisible to the agent, and Playwright's CSS engine dispatches to
# them fine (it pierces open shadow DOM), so cataloging them is enough.
SNAPSHOT_JS = """
([start]) => {
  const out = {url: location.href, title: document.title, elements: [], text: ""};
  const TAGS = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);
  const ROLES = new Set(['button', 'link', 'checkbox', 'tab', 'textbox', 'combobox']);
  let i = start || 0;
  let truncated = false;
  const interactive = (el) => TAGS.has(el.tagName)
    || ROLES.has((el.getAttribute('role') || '').toLowerCase());
  const collect = (el, shadow) => {
    const ref = `@e${++i}`;
    el.setAttribute('data-jambu-ref', ref);
    const name = (el.innerText || el.value || el.getAttribute('aria-label')
                  || el.getAttribute('placeholder') || el.getAttribute('title')
                  || '').trim().slice(0, 120);
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0
                    && style.visibility !== 'hidden' && style.display !== 'none';
    out.elements.push({
      ref, tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '', type: el.getAttribute('type') || '',
      name, href: el.href || '',
      value: (typeof el.value === 'string' ? el.value.slice(0, 200) : ''),
      visible, disabled: !!el.disabled, checked: !!el.checked,
      shadow: !!shadow,
    });
    if (out.elements.length >= %d) { truncated = true; return true; }
    return false;
  };
  const walk = (root, shadow) => {
    let nodes;
    try { nodes = root.querySelectorAll('*'); } catch (e) { return false; }
    for (const el of nodes) {
      try {
        if (interactive(el) && collect(el, shadow)) return true;
        if (el.shadowRoot && walk(el.shadowRoot, true)) return true;
      } catch (e) { /* detached node mid-navigation: skip it */ }
    }
    return false;
  };
  walk(document, false);
  out.truncated = truncated;
  out.text = (document.body ? document.body.innerText : '').slice(0, %d);
  return out;
}
""" % (MAX_ELEMENTS, MAX_TEXT_CHARS)



# ---------------------------------------------------------------------------
# Page adapters
# ---------------------------------------------------------------------------

class PageAdapter(Protocol):
    async def goto(self, url: str) -> None: ...
    async def snapshot(self) -> dict: ...
    async def click(self, ref: str) -> None: ...
    async def type_text(self, ref: str, text: str) -> None: ...
    async def current_url(self) -> str: ...


class Telemetry:
    """Bounded per-session buffer of console/network/page signals.

    Collected between steps and drained into every flow report, so an agent
    never has to spend a separate tool call asking "were there console
    errors?". Bounded on all axes to keep payloads small.
    """

    def __init__(self, cap: int = MAX_TELEMETRY):
        self.cap = cap
        self.console: list[dict] = []
        self.page_errors: list[str] = []
        self.failed_requests: list[dict] = []
        self.bad_responses: list[dict] = []
        self.dialogs: list[dict] = []

    @staticmethod
    def _trim(seq: list, cap: int) -> None:
        if len(seq) > cap:
            del seq[: len(seq) - cap]

    def add_console(self, level: str, text: str, location: Optional[dict] = None) -> None:
        self.console.append({
            "level": level, "text": (text or "")[:300],
            "location": location or {},
        })
        self._trim(self.console, self.cap)

    def add_page_error(self, text: str) -> None:
        self.page_errors.append((text or "")[:300])
        self._trim(self.page_errors, self.cap)

    def add_failed_request(self, method: str, url: str, failure: str) -> None:
        self.failed_requests.append({
            "method": method, "url": (url or "")[:300], "failure": (failure or "")[:200],
        })
        self._trim(self.failed_requests, self.cap)

    def add_response(self, method: str, url: str, status: int) -> None:
        self.bad_responses.append({
            "method": method, "url": (url or "")[:300], "status": status,
        })
        self._trim(self.bad_responses, self.cap)

    def add_dialog(self, kind: str, message: str, *, accepted: bool,
                   text: str = "") -> None:
        """Record a native dialog (alert/confirm/prompt/beforeunload).

        Dialogs are *interaction*, not just noise: an unhandled ``confirm()``
        silently swallows the click that raised it, so flows need to see them.
        """
        self.dialogs.append({
            "type": (kind or "dialog"), "message": (message or "")[:200],
            "accepted": bool(accepted), "text": (text or "")[:100],
        })
        self._trim(self.dialogs, self.cap)

    def errors(self) -> list[str]:
        """Console errors + uncaught page errors as plain strings."""
        out = [c["text"] for c in self.console if c.get("level") == "error"]
        return out + list(self.page_errors)

    def snapshot(self) -> dict:
        return {
            "console_errors": self.errors(),
            "console_errors_detail": (
                [{"text": c["text"], "location": c.get("location") or {}}
                 for c in self.console if c.get("level") == "error"]
                + [{"text": e, "location": {}} for e in self.page_errors]
            ),
            "console_warnings": [c["text"] for c in self.console if c.get("level") == "warning"],
            "failed_requests": list(self.failed_requests),
            "bad_responses": list(self.bad_responses),
            "dialogs": list(self.dialogs),
        }

    def drain(self) -> dict:
        data = self.snapshot()
        self.console.clear()
        self.page_errors.clear()
        self.failed_requests.clear()
        self.bad_responses.clear()
        self.dialogs.clear()
        return data


class PlaywrightPage:
    """Adapter over a Playwright page (created by ``BrowserSession``).

    Attaches telemetry listeners at construction so console errors, uncaught
    exceptions, failed requests and >=400 responses are captured continuously
    and can be drained per flow rather than fetched with extra calls.
    """

    def __init__(self, page, network: Optional[dict] = None,
                 network_policy: Optional[NetworkPolicy] = None):
        self._page = page
        self.telemetry = Telemetry()
        self._network = network or {}
        self._network_rules = compile_network(network)
        self.network_policy = network_policy
        self._route_installed = False
        self._route_target = getattr(page, "context", None) or page
        # BrowserContext is needed for CDP (throttling) and coverage; kept
        # explicitly rather than reaching through page.context every call.
        self._context = getattr(page, "context", None)
        self.requests: list[dict] = []
        try:
            page.on("console", lambda msg: self.telemetry.add_console(
                msg.type, msg.text,
                getattr(msg, "location", None) if isinstance(
                    getattr(msg, "location", None), dict) else None,
            ))
            page.on("pageerror", lambda exc: self.telemetry.add_page_error(str(exc)))
            page.on("requestfailed", lambda req: self.telemetry.add_failed_request(
                req.method, req.url,
                (req.failure or "") if isinstance(req.failure, str) else str(req.failure or ""),
            ))
            page.on("response", lambda resp: self.telemetry.add_response(
                resp.request.method, resp.url, resp.status,
            ) if resp.status >= 400 else None)
            page.on("request", lambda req: self._track_request(req.method, req.url))
        except Exception:  # adapters/fakes without event support
                log.debug("page event listeners unavailable on this adapter",
                          exc_info=True)
        # Ref → frame index recorded by the last snapshot, so acting on an
        # element inside an iframe dispatches inside that frame.
        self._ref_frames: dict[str, int] = {}
        # One-shot dialog policy armed by a step. Default is *dismiss*, which
        # matches Playwright's own behaviour: a stray confirm() on a destructive
        # action must never be auto-accepted on the agent's behalf.
        self._dialog_policy: Optional[dict] = None
        try:
            page.on("dialog", self._on_dialog)
        except Exception:  # adapters/fakes without dialog events
                log.debug("dialog events unavailable on this adapter",
                          exc_info=True)

    async def _on_dialog(self, dialog) -> None:
        """Record every native dialog and apply the armed policy (else dismiss).

        Recording matters as much as answering: without this, an unhandled
        ``confirm()`` looks to the agent like a click that did nothing.
        """
        policy = self._dialog_policy or {}
        self._dialog_policy = None
        accept = bool(policy.get("accept"))
        kind = str(getattr(dialog, "type", "dialog") or "dialog")
        message = str(getattr(dialog, "message", "") or "")
        text = str(policy.get("text") or "")
        try:
            if accept and kind == "prompt" and text:
                await dialog.accept(text)
            elif accept:
                await dialog.accept()
            else:
                await dialog.dismiss()
        except Exception:  # already handled by the page, or page is closing
                # Dialog already handled by the page, or the page is closing.
                log.debug("dialog answer did not apply", exc_info=True)
        self.telemetry.add_dialog(kind, message, accepted=accept, text=text)

    def arm_dialog(self, accept: bool = True, text: str = "") -> None:
        """Arm the *next* dialog raised by a subsequent action."""
        self._dialog_policy = {"accept": bool(accept), "text": text or ""}

    def _track_request(self, method: str, url: str) -> None:
        self.requests.append({"method": method, "url": (url or "")[:300]})
        if len(self.requests) > MAX_TELEMETRY:
            del self.requests[: len(self.requests) - MAX_TELEMETRY]

    def made_request(self, pattern: str) -> bool:
        return any(pattern in r["url"] for r in self.requests)

    def drain_requests(self) -> list[dict]:
        out = list(self.requests)
        self.requests.clear()
        return out

    async def setup_network(self, network: Optional[dict]) -> dict:
        """Install request interception plus the mandatory request policy."""
        self._network = network or {}
        self._network_rules = compile_network(network)
        offline = bool(self._network.get("offline"))
        if self._route_installed:
            return {"rules": len(self._network_rules), "offline": offline,
                    "supported": True, "policy": True,
                    "websocket_supported": self.network_policy._websocket_supported}
        if self.network_policy is None:
            return {"rules": len(self._network_rules), "offline": offline,
                    "supported": False, "policy": False}

        async def handler(route):
            request = route.request
            decision = await asyncio.to_thread(
                self.network_policy.decide,
                request.url,
                method=request.method,
                kind=getattr(request, "resource_type", "http"),
            )
            if not decision.allowed:
                return await route.abort("blockedbyclient")
            rule = match_network(self._network_rules, request.url, request.method)
            if rule is not None:
                if rule.action == "abort":
                    return await route.abort()
                if rule.action == "delay":
                    await asyncio.sleep(max(0, rule.ms) / 1000)
                    return await route.continue_()
                if rule.action == "fulfill":
                    return await route.fulfill(
                        status=rule.status,
                        body=serialize_body(rule.body, rule.content_type),
                        content_type=rule.content_type,
                    )
            if self._network.get("offline"):
                return await route.abort()
            return await route.continue_()

        try:
            await self._route_target.route("**/*", handler)
        except Exception:  # adapter without routing support
            return {"rules": len(self._network_rules), "offline": offline,
                    "supported": False, "policy": True}
        websocket_supported = False
        route_websocket = getattr(self._route_target, "route_web_socket", None)
        if callable(route_websocket):
            async def websocket_handler(websocket_route):
                decision = await asyncio.to_thread(
                    self.network_policy.decide,
                    websocket_route.url, method="GET", kind="websocket",
                )
                if not decision.allowed:
                    return await websocket_route.close(code=1008, reason=decision.reason)
                return await websocket_route.connect_to_server()
            try:
                await route_websocket("**/*", websocket_handler)
                websocket_supported = True
            except Exception:
                websocket_supported = False
        self.network_policy._websocket_supported = websocket_supported
        self.network_policy._routing_installed = True
        self._route_installed = True
        return {"rules": len(self._network_rules), "offline": offline,
                "supported": True, "policy": True,
                "websocket_supported": websocket_supported}

    async def evaluate(self, script: str, arg: Any = None):
        if arg is None:
            return await self._page.evaluate(script)
        return await self._page.evaluate(script, arg)

    async def fetch_text(self, url: str) -> Optional[str]:
        """Fetch a URL from within the page origin (used for source maps)."""
        return await self._page.evaluate(
            """async (u) => {
                try { const r = await fetch(u); return r.ok ? await r.text() : null; }
                catch (e) { return null; }
            }""",
            url,
        )

    async def http_request(self, method: str, url: str, *,
                           headers: Optional[dict] = None,
                           body: Any = None,
                           timeout_ms: int = 15000) -> dict:
        """Issue an API call from the browser context (shared cookies).

        Uses Playwright's APIRequestContext attached to the page's
        BrowserContext, so session cookies set by the UI are sent — which is
        what makes "click, then verify the backend" checks meaningful.
        Falls back to an in-page ``fetch`` when the adapter lacks it.
        """
        method = (method or "GET").upper()
        headers = {k: str(v) for k, v in (headers or {}).items()}
        if self.network_policy is not None:
            decision = await asyncio.to_thread(
                self.network_policy.decide, url, method=method, kind="api"
            )
            if not decision.allowed:
                return {"status": 0, "ok": False, "method": method, "url": url,
                        "latency_ms": 0, "error": f"blocked_{decision.reason}: {decision.detail}",
                        "json": None, "text": "", "headers": {},
                        "blocked": True, "reason": decision.reason}
        context = getattr(self._page, "context", None)
        request_ctx = getattr(context, "request", None)
        if request_ctx is None:
            request_ctx = getattr(self._page, "request", None)
        started = time.time()
        if request_ctx is not None:
            kwargs: dict = {"headers": headers, "timeout": timeout_ms}
            if body is not None:
                if isinstance(body, (dict, list)):
                    kwargs["data"] = body
                else:
                    kwargs["data"] = str(body)
            current_url = url
            current_method = method
            for redirect_count in range(6):
                response = await request_ctx.fetch(
                    current_url, method=current_method, max_redirects=0, **kwargs
                )
                if not 300 <= response.status < 400:
                    break
                redirect_location = (response.headers or {}).get("location", "")
                if not redirect_location:
                    break
                destination = urljoin(current_url, redirect_location)
                decision = await asyncio.to_thread(
                    self.network_policy.decide,
                    destination, method=current_method, kind="redirect",
                ) if self.network_policy is not None else NetworkDecision(
                    True, "adapter_without_request_policy"
                )
                if not decision.allowed:
                    latency_ms = int((time.time() - started) * 1000)
                    return {
                        "status": 0, "ok": False, "method": method,
                        "url": destination, "latency_ms": latency_ms,
                        "error": f"blocked_redirect: {decision.reason}: {decision.detail}",
                        "json": None, "text": "", "headers": {},
                        "blocked": True, "reason": decision.reason,
                    }
                if redirect_count == 5:
                    latency_ms = int((time.time() - started) * 1000)
                    return {
                        "status": 0, "ok": False, "method": method,
                        "url": current_url, "latency_ms": latency_ms,
                        "error": "blocked_redirect: redirect chain exceeds 5 hops",
                        "json": None, "text": "", "headers": {},
                        "blocked": True, "reason": "redirect_loop",
                    }
                if response.status in (301, 302, 303) and current_method not in ("GET", "HEAD"):
                    current_method = "GET"
                    kwargs.pop("data", None)
                current_url = destination
            latency_ms = int((time.time() - started) * 1000)
            text = ""
            try:
                text = await response.text()
            except Exception:
                text = ""
            parsed = None
            try:
                parsed = json.loads(text) if text else None
            except ValueError:
                parsed = None
            return {
                "status": response.status, "ok": response.ok,
                "method": method, "url": url, "latency_ms": latency_ms,
                "json": parsed, "text": text[:4000],
                "headers": dict(response.headers or {}),
            }
        # Fallback: in-page fetch (same origin/cookies, no custom headers
        # beyond what the browser permits).
        result = await self._page.evaluate(
            """async ({method, url, headers, body}) => {
                try {
                    const r = await fetch(url, {method, headers,
                        body: body === null ? undefined : body});
                    const text = await r.text();
                    let json = null;
                    try { json = JSON.parse(text); } catch (e) {}
                    return {status: r.status, ok: r.ok, text, json};
                } catch (e) { return {error: String(e)}; }
            }""",
            {"method": method, "url": url, "headers": headers,
             "body": None if body is None else (
                 body if isinstance(body, str) else json.dumps(body))},
        )
        latency_ms = int((time.time() - started) * 1000)
        if result.get("error"):
            return {"status": 0, "ok": False, "method": method, "url": url,
                    "latency_ms": latency_ms, "error": result["error"],
                    "json": None, "text": "", "headers": {}}
        return {
            "status": result.get("status", 0), "ok": bool(result.get("ok")),
            "method": method, "url": url, "latency_ms": latency_ms,
            "json": result.get("json"), "text": (result.get("text") or "")[:4000],
            "headers": {},
        }

    async def a11y_audit(self) -> dict:
        return await self._page.evaluate(A11Y_JS)

    async def perf_metrics(self) -> dict:
        return await self._page.evaluate(PERF_JS)

    async def resource_count(self) -> int:
        return await self._page.evaluate(
            "() => performance.getEntriesByType('resource').length"
        )

    async def inject_css(self, css: str) -> None:
        await self._page.add_style_tag(content=css)

    async def init_perf_observers(self) -> None:
        await self._page.add_init_script(PERF_OBSERVER_JS)

    # -- determinism: clock + network shaping + coverage ---------------------

    async def install_clock(self, clock: dict) -> dict:
        """Freeze/advance the page clock so time-dependent flows are stable.

        ``clock`` accepts ``{"time": "2026-01-01T09:00:00Z"}`` and/or
        ``{"rate": 0}`` (frozen) / ``{"rate": 1}`` (realtime). Requires
        Playwright's ``page.clock`` (Chromium). Returns what was installed so
        the flow report can state the determinism it ran under.
        """
        if not hasattr(self._page, "clock"):
            raise SessionRefused(
                "unsupported_action",
                "page adapter does not support clock installation (needs Chromium)",
            )
        spec = dict(clock or {})
        installed: dict = {}
        if spec.get("time"):
            await self._page.clock.install(time=spec["time"])
            installed["time"] = spec["time"]
        if "rate" in spec:
            if spec["rate"] == 0:
                await self._page.clock.pause()
            else:
                await self._page.clock.resume()
            installed["rate"] = spec["rate"]
        return installed

    async def set_throttle(self, throttle: dict) -> dict:
        """Emulate a network profile via CDP (offline/3G/4G/latency).

        ``{"offline": true}`` or ``{"download_kbps": 400, "upload_kbps": 200,
        "latency_ms": 300}``. Uses a raw CDP session, so it is Chromium-only;
        other engines simply get an ``unsupported_action`` and the flow keeps
        running (the report records that shaping was skipped).
        """
        spec = dict(throttle or {})
        session = await self._cdp_session()
        try:
            if spec.get("offline"):
                await session.send("Network.enable")
                await session.send("Network.emulateNetworkConditions", {
                    "offline": True, "latency": 0,
                    "downloadThroughput": -1, "uploadThroughput": -1,
                })
            elif spec:
                await session.send("Network.enable")
                await session.send("Network.emulateNetworkConditions", {
                    "offline": False,
                    "latency": int(spec.get("latency_ms", 0)),
                    "downloadThroughput": int(spec.get("download_kbps", -1)) * 1024 // 8,
                    "uploadThroughput": int(spec.get("upload_kbps", -1)) * 1024 // 8,
                })
        finally:
            try:
                await session.detach()
            except Exception:  # noqa: BLE001 - detaching must never fail a flow
                log.debug("cdp session detach failed (throttle cleanup)",
                          exc_info=True)
        return spec

    async def _cdp_session(self):
        """A raw CDP session, or a clear refusal when unavailable."""
        context = self._context or getattr(self._page, "context", None)
        if context is None or not hasattr(context, "new_cdp_session"):
            raise SessionRefused(
                "unsupported_action",
                "CDP features are unavailable (needs a Chromium browser context)",
            )
        return await context.new_cdp_session(self._page)

    async def start_coverage(self) -> dict:
        """Begin precise JS coverage over CDP (Chromium).

        Playwright removed ``page.coverage``, so coverage rides on
        ``Profiler.startPreciseCoverage`` — the same CDP channel as
        network throttling.
        """
        session = await self._cdp_session()
        await session.send("Profiler.enable")
        await session.send("Profiler.startPreciseCoverage", {
            "callCount": False, "detailed": True,
        })
        self._coverage_session = session
        return {"started": True}

    async def stop_coverage(self) -> dict:
        """Stop coverage and summarise uncovered bytes per script."""
        session = getattr(self, "_coverage_session", None)
        if session is None:
            raise SessionRefused(
                "unsupported_action", "coverage was not started on this page"
            )
        try:
            taken = await session.send("Profiler.takePreciseCoverage")
        finally:
            for cmd in ("Profiler.stopPreciseCoverage", "Profiler.disable"):
                try:
                    await session.send(cmd)
                except Exception:  # noqa: BLE001 - teardown must not fail a flow
                    log.debug(f"coverage teardown {cmd} failed", exc_info=True)
            try:
                await session.detach()
            except Exception:  # noqa: BLE001
                log.debug("cdp session detach failed (coverage cleanup)",
                          exc_info=True)
            self._coverage_session = None
        return summarise_coverage((taken or {}).get("result") or [])

    async def goto(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)

    # -- catalog: multi-frame + shadow DOM -----------------------------------

    @staticmethod
    def _ref_selector(ref: str) -> str:
        return f'[data-jambu-ref="{ref}"]'

    def _target_frame(self, ref: str):
        """The frame that owns ``ref`` in the last snapshot (main frame default)."""
        index = self._ref_frames.get(ref, 0)
        if not index:
            return self._page
        frames = list(getattr(self._page, "frames", None) or [])
        if 0 <= index < len(frames):
            return frames[index]
        return self._page

    async def snapshot(self) -> dict:
        frames = list(getattr(self._page, "frames", None) or [])
        if len(frames) > 1:
            return await self._snapshot_frames(frames[:MAX_FRAMES])
        self._ref_frames = {}
        return await self._page.evaluate(SNAPSHOT_JS, [0])

    async def _snapshot_frames(self, frames: list) -> dict:
        """Collect one catalog across same-process frames with continuing refs.

        Iframes hold a lot of real UI (payments, auth, uploads, embedded
        editors). Cataloging only the top document makes those invisible, so
        refs are numbered globally and each element records its frame index for
        dispatch. Frames that refuse to evaluate (detached, cross-process) are
        reported instead of silently dropping content.
        """
        out: dict = {"url": self._page.url, "title": "", "elements": [],
                     "text": "", "frames": []}
        ref_frames: dict[str, int] = {}
        count = 0
        for index, frame in enumerate(frames):
            url = str(getattr(frame, "url", "") or "")
            try:
                raw = await frame.evaluate(SNAPSHOT_JS, [count]) or {}
            except Exception:
                out["frames"].append({"i": index, "url": url[:120], "error": True})
                continue
            elements = raw.get("elements") or []
            for element in elements:
                if index:
                    element["frame"] = index
                    element["frame_url"] = url[:120]
            ref_frames.update({e["ref"]: index for e in elements if e.get("ref")})
            count += len(elements)
            out["elements"].extend(elements)
            if index == 0:
                out["title"] = raw.get("title") or ""
                out["text"] = raw.get("text") or ""
            out["frames"].append({"i": index, "url": url[:120], "elements": len(elements)})
            if count >= MAX_ELEMENTS:
                break
        out["truncated"] = bool(count >= MAX_ELEMENTS)
        self._ref_frames = ref_frames
        return out

    async def click(self, ref: str) -> None:
        await self._target_frame(ref).click(self._ref_selector(ref), timeout=10000)

    async def type_text(self, ref: str, text: str) -> None:
        await self._target_frame(ref).fill(self._ref_selector(ref), text, timeout=10000)

    async def current_url(self) -> str:
        return self._page.url

    # -- uploads / downloads --------------------------------------------------

    async def set_input_files(self, ref: str, files: list,
                              timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS) -> dict:
        """Attach files directly to an ``<input type=file>`` (no chooser)."""
        await self._target_frame(ref).set_input_files(
            self._ref_selector(ref), list(files), timeout=timeout_ms,
        )
        return {"uploaded": len(files), "mode": "input"}

    async def set_input_files_selector(self, selector: str, files: list,
                                       timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS) -> dict:
        await self._page.set_input_files(
            self._engine_selector(selector), list(files), timeout=timeout_ms,
        )
        return {"uploaded": len(files), "mode": "input"}

    async def upload_via_chooser(self, ref: str, files: list,
                                 timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS) -> dict:
        """Click an element that opens the OS file picker, then feed it files."""
        frame = self._target_frame(ref)
        async with self._page.expect_file_chooser(timeout=timeout_ms) as info:
            await frame.click(self._ref_selector(ref), timeout=timeout_ms)
        chooser = await info.value
        await chooser.set_files(list(files))
        return {"uploaded": len(files), "mode": "chooser"}

    async def download_via_click(self, ref: str, dest_dir: str,
                                 timeout_ms: int = 15000,
                                 match: str = "") -> dict:
        """Click a link/button that starts a download and save it to *dest_dir*."""
        frame = self._target_frame(ref)
        async with self._page.expect_download(timeout=timeout_ms) as info:
            await frame.click(self._ref_selector(ref), timeout=timeout_ms)
        return await self._store_download(await info.value, dest_dir, match)

    async def download_via_selector(self, selector: str, dest_dir: str,
                                    timeout_ms: int = 15000,
                                    match: str = "") -> dict:
        async with self._page.expect_download(timeout=timeout_ms) as info:
            await self._page.click(self._engine_selector(selector), timeout=timeout_ms)
        return await self._store_download(await info.value, dest_dir, match)

    @staticmethod
    async def _store_download(download, dest_dir: str, match: str = "") -> dict:
        """Persist a Playwright download inside *dest_dir* with a content digest."""
        import fnmatch

        name = os.path.basename(str(getattr(download, "suggested_filename", "")
                                    or "download.bin"))
        info: dict = {
            "file": name, "url": str(getattr(download, "url", "") or "")[:300],
        }
        if match and not fnmatch.fnmatch(name, match):
            info["mismatch"] = match
            return info
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, name)
        await download.save_as(path)
        info["path"] = path
        info["bytes"] = os.path.getsize(path)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for _ in range(40):  # fingerprint the first 5 MB, not the whole disk
                chunk = handle.read(131072)
                if not chunk:
                    break
                digest.update(chunk)
        info["sha256_12"] = digest.hexdigest()[:12]
        return info

    async def wait_for_function(self, script: str,
                                timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS) -> None:
        await self._page.wait_for_function(script, timeout=timeout_ms)

    # -- selector dispatch (CSS/XPath direct addressing) ---------------------

    @staticmethod
    def _engine_selector(selector: str) -> str:
        selector = (selector or "").strip()
        if selector.startswith("xpath="):
            return selector
        if selector.startswith(("//", "(//")):
            return f"xpath={selector}"
        return selector

    async def click_selector(self, selector: str) -> None:
        await self._page.click(self._engine_selector(selector), timeout=10000)

    async def fill_selector(self, selector: str, text: str) -> None:
        await self._page.fill(self._engine_selector(selector), text, timeout=10000)

    async def press_selector(self, selector: str, key: str) -> None:
        await self._page.press(self._engine_selector(selector), key, timeout=10000)

    async def hover_selector(self, selector: str) -> None:
        await self._page.hover(self._engine_selector(selector), timeout=10000)

    async def select_selector(self, selector: str, value: str) -> None:
        await self._page.select_option(self._engine_selector(selector), value, timeout=10000)

    async def check_selector(self, selector: str, checked: bool = True) -> None:
        await self._page.set_checked(self._engine_selector(selector), checked, timeout=10000)

    async def is_visible_selector(self, selector: str) -> bool:
        return bool(await self._page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            }""",
            self._engine_selector(selector),
        ))

    async def text_of_selector(self, selector: str) -> str:
        return await self._page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || el.textContent || '') : '';
            }""",
            self._engine_selector(selector),
        ) or ""

    async def value_of_selector(self, selector: str) -> str:
        return await self._page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return el && typeof el.value === 'string' ? el.value : ''; }",
            self._engine_selector(selector),
        ) or ""

    async def count_selector(self, selector: str) -> int:
        return int(await self._page.evaluate(
            "(sel) => document.querySelectorAll(sel).length",
            self._engine_selector(selector),
        ) or 0)

    async def is_enabled_selector(self, selector: str) -> bool:
        return bool(await self._page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return !!el && !el.disabled; }",
            self._engine_selector(selector),
        ))

    async def is_checked_selector(self, selector: str) -> bool:
        return bool(await self._page.evaluate(
            "(sel) => { const el = document.querySelector(sel); return !!el && !!el.checked; }",
            self._engine_selector(selector),
        ))

    async def eval_js(self, script: str):
        return await self._page.evaluate(script)

    # -- optional capabilities (used by the flow runner when present) ---------

    async def wait_for(self, *, timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS) -> None:
        await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)

    async def wait_for_selector(self, selector: str,
                                timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS) -> None:
        await self._page.wait_for_selector(selector, timeout=timeout_ms)

    async def wait_for_text(self, text: str,
                            timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS) -> None:
        await self._page.get_by_text(text, exact=False).first.wait_for(
            state="visible", timeout=timeout_ms,
        )

    async def press(self, ref: str, key: str) -> None:
        if not ref:
            await self._page.press("body", key, timeout=10000)
            return
        await self._target_frame(ref).press(self._ref_selector(ref), key, timeout=10000)

    async def hover(self, ref: str) -> None:
        await self._target_frame(ref).hover(self._ref_selector(ref), timeout=10000)

    async def select_option(self, ref: str, value: str) -> None:
        await self._target_frame(ref).select_option(
            self._ref_selector(ref), value, timeout=10000,
        )

    async def check(self, ref: str, checked: bool = True) -> None:
        await self._target_frame(ref).set_checked(
            self._ref_selector(ref), checked, timeout=10000,
        )

    async def reload(self) -> None:
        await self._page.reload(wait_until="domcontentloaded", timeout=20000)

    async def go_back(self) -> None:
        await self._page.go_back(wait_until="domcontentloaded", timeout=20000)

    async def go_forward(self) -> None:
        await self._page.go_forward(wait_until="domcontentloaded", timeout=20000)

    async def screenshot(self, full_page: bool = False) -> str:
        import base64

        raw = await self._page.screenshot(full_page=full_page)
        return base64.b64encode(raw).decode("ascii")

    def drain_telemetry(self) -> dict:
        return self.telemetry.drain()

    def peek_telemetry(self) -> dict:
        return self.telemetry.snapshot()


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class Step:
    seq: int
    ts: float
    action: str
    outcome: str            # ok | blocked | reverted
    url: str
    ref: Optional[str] = None
    detail: str = ""
    prev_hash: Optional[str] = None
    step_hash: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "seq": self.seq, "ts": self.ts, "action": self.action,
            "outcome": self.outcome, "url": self.url, "ref": self.ref,
            "detail": self.detail, "prev_hash": self.prev_hash,
            "step_hash": self.step_hash,
        }


# SessionRefused now lives in browser_agent_errors so extracted subsystems can
# raise it without importing this module (see that file's docstring).
from backend.modules.browser_agent_errors import SessionRefused  # noqa: E402
# Re-exported so existing importers keep working while the implementation
# lives with the assertion engine that owns it.
from backend.modules.browser_assertions import json_path as _json_path  # noqa: E402,F401
from backend.modules.browser_step_actions import (  # noqa: E402
    run_api_step,
    run_dialog_and_navigation,
    run_file_step,
    run_interaction,
)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def host_allowed(host: str, allow_domains: list[str]) -> bool:
    """Exact host or subdomain match against the session allowlist."""
    host = (host or "").lower()
    if not host:
        return False
    for domain in allow_domains:
        domain = domain.lower().lstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def classify_risk(*parts: str) -> Optional[str]:
    """Return the matched risky phrase, or None for ordinary elements."""
    blob = " ".join(p or "" for p in parts).lower()
    for pattern in RISKY_PATTERNS:
        if pattern in blob:
            return pattern.strip()
    return None



def estimate_tokens(payload) -> int:
    """Rough token estimate for a payload (~4 chars per token).

    Reports how much context a flow report actually costs the agent, so
    token claims are measured, not marketing.
    """
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, separators=(",", ":"))
        except Exception:
            text = str(payload)
    return max(1, len(text) // 4)


_FLOW_BLOCKED_REASONS = {
    "approval_required", "blocked_domain", "blocked_protocol", "human_takeover",
    "private_address", "unsafe_url", "invalid_url", "target_required",
    "unknown_ref", "dns_resolution_failed", "redirect_loop",
    "upload_path_denied", "upload_limit",
}


# ---------------------------------------------------------------------------
# Local file access: uploads in, downloads out
# ---------------------------------------------------------------------------

UPLOAD_ROOTS_ENV = "JAMBU_UPLOAD_ROOTS"
DOWNLOAD_DIR_ENV = "JAMBU_DOWNLOAD_DIR"


def _roots(raw: str) -> list[str]:
    parts = [p for p in (raw or "").split(os.pathsep) if p.strip()]
    return [os.path.realpath(os.path.expanduser(p)) for p in parts]


def upload_roots() -> list[str]:
    """Directories an agent may read files *from* (default: the working dir).

    An upload step reads from the machine running the engine, so without a root
    fence a flow could ship ``~/.ssh/id_rsa`` through a contact form.
    """
    return _roots(os.environ.get(UPLOAD_ROOTS_ENV, "")) or [os.path.realpath(os.getcwd())]


def resolve_upload_paths(files: Any, *, roots: Optional[list[str]] = None) -> list[str]:
    """Validate agent-supplied upload paths; raise :class:`SessionRefused`.

    Every path must resolve inside a configured root, exist, and be a regular
    file under :data:`MAX_UPLOAD_BYTES`. Count is capped by MAX_UPLOAD_FILES.
    """
    if isinstance(files, str):
        files = [p for p in files.split(os.pathsep) if p.strip()]
    if not isinstance(files, list) or not files:
        raise SessionRefused("invalid_step", "upload requires a non-empty 'files' list")
    if len(files) > MAX_UPLOAD_FILES:
        raise SessionRefused(
            "upload_limit", f"at most {MAX_UPLOAD_FILES} files per upload step",
        )
    allowed = roots if roots is not None else upload_roots()
    out: list[str] = []
    for item in files:
        if not isinstance(item, str) or not item.strip():
            raise SessionRefused("invalid_step", "upload file entries must be strings")
        candidate = os.path.realpath(os.path.expanduser(item.strip()))
        if not any(
            candidate == root or candidate.startswith(root + os.sep)
            for root in allowed
        ):
            raise SessionRefused(
                "upload_path_denied",
                f"{item!r} is outside the upload roots "
                f"({UPLOAD_ROOTS_ENV}={os.pathsep.join(allowed)})",
            )
        if not os.path.isfile(candidate):
            raise SessionRefused("upload_path_denied", f"not a readable file: {item!r}")
        size = os.path.getsize(candidate)
        if size > MAX_UPLOAD_BYTES:
            raise SessionRefused(
                "upload_limit", f"{item!r} is {size} bytes (cap {MAX_UPLOAD_BYTES})",
            )
        out.append(candidate)
    return out


def download_dir_for(session_id: str, artifacts_dir: Optional[str] = None) -> str:
    """Where saved downloads land: the session's artifact dir when it has one."""
    if artifacts_dir:
        return artifacts_dir
    configured = _roots(os.environ.get(DOWNLOAD_DIR_ENV, ""))
    base = configured[0] if configured else os.path.join(
        tempfile.gettempdir(), f"jambu-downloads-{session_id}",
    )
    os.makedirs(base, exist_ok=True)
    return base


def estimate_primitive_cost(steps: list[dict], elements: int = 40,
                            text_chars: int = 600) -> dict:
    """What the same work costs as a primitive snapshot/act loop.

    The comparison is deliberately generous to the loop: one open + one close,
    one act per step, and a fresh snapshot before every step that needs refs
    (a loop cannot know refs changed, so it re-observes). A snapshot is charged
    as ``60 + 15*min(elements, 25) + text/4`` tokens, matching the shape of the
    real snapshot renderer. Honest numbers, not marketing: if the flow's own
    report is bigger than this estimate, the flow did not save anything.
    """
    acts = len(steps) or 1
    mutating = sum(
        1 for s in steps
        if isinstance(s, dict) and (s.get("action") or "").lower() in _MUTATING_ACTIONS
    )
    snapshots = 1 + mutating
    snapshot_tokens = 60 + 15 * min(max(elements, 0), 25) + max(text_chars, 0) // 4
    calls = 1 + 1 + acts + snapshots
    tokens = 28 + 10 + acts * 12 + snapshots * snapshot_tokens
    return {"calls": calls, "tokens": tokens}


# Engine-lifetime tally of what flows saved versus a primitive loop, so the
# "token-efficient" claim is a number the product can show rather than assert.
_METER: dict = {"runs": 0, "steps": 0, "flow_tokens": 0,
                "primitive_tokens": 0, "calls_avoided": 0}


def record_flow_savings(savings: dict) -> dict:
    """Add one flow's savings to the engine tally and return the running total."""
    _METER["runs"] += 1
    _METER["steps"] += int(savings.get("steps", 0) or 0)
    _METER["flow_tokens"] += int(savings.get("flow_tokens", 0) or 0)
    _METER["primitive_tokens"] += int(savings.get("primitive_tokens", 0) or 0)
    _METER["calls_avoided"] += int(savings.get("calls_avoided", 0) or 0)
    return token_savings()


def token_savings() -> dict:
    """Running totals: tokens/calls a primitive loop would have spent."""
    saved = max(0, _METER["primitive_tokens"] - _METER["flow_tokens"])
    return {
        "runs": _METER["runs"],
        "steps": _METER["steps"],
        "flow_tokens": _METER["flow_tokens"],
        "primitive_tokens": _METER["primitive_tokens"],
        "saved_tokens": saved,
        "calls_avoided": _METER["calls_avoided"],
    }


def reset_token_savings() -> None:
    """Test hook: zero the engine tally."""
    for key in _METER:
        _METER[key] = 0



def classify_step_failure(reason: Optional[str]) -> str:
    """Classify a step failure without changing the historical ``ok`` field."""
    if reason in _FLOW_BLOCKED_REASONS:
        return "blocked"
    if reason in {"error", "harness_error", "timeout", "page_closed"}:
        return "inconclusive"
    return "failed"


def _merged_length(intervals: list) -> int:
    """Total length of the union of (start, end) byte ranges."""
    total = 0
    cur_s = cur_e = None
    for s0, e0 in sorted(intervals):
        if cur_e is None or s0 > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s0, e0
        else:
            cur_e = max(cur_e, e0)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def _subtract_ranges(base: list, holes: list) -> list:
    """``base`` intervals minus ``holes`` intervals (holes win)."""
    result = []
    for start, end in base:
        pieces = [(start, end)]
        for hs, he in holes:
            nxt = []
            for ps, pe in pieces:
                if he <= ps or hs >= pe:
                    nxt.append((ps, pe))
                    continue
                if hs > ps:
                    nxt.append((ps, min(hs, pe)))
                if he < pe:
                    nxt.append((max(he, ps), pe))
            pieces = nxt
        result.extend(pieces)
    return [iv for iv in result if iv[1] > iv[0]]


def summarise_coverage(entries: list) -> dict:
    """CDP ``Profiler.takePreciseCoverage`` entries → used/total bytes/script.

    With ``detailed=True`` Chromium reports **one entry per function**, not
    per script, so entries are regrouped by URL (inline scripts by scriptId)
    before the arithmetic: file size is the highest endOffset in the group,
    used bytes is the *union* of the ranges whose ``count > 0``.

    What a reader actually wants is the unused gap, so scripts are sorted
    worst-first. Playwright removed ``page.coverage``, which is why this
    rides on raw CDP. ``extensions::`` entries are Chrome's own injected JS
    and are excluded; inline scripts legitimately have no URL and are kept.
    """
    groups: dict = {}
    for entry in entries or []:
        url = (entry or {}).get("url") or ""
        if url.startswith("extensions::"):
            continue
        key = url or f"inline#{entry.get('scriptId')}"
        group = groups.setdefault(
            key, {"url": url, "used": [], "dead": [], "total": 0}
        )
        for fn in entry.get("functions") or []:
            for rng in fn.get("ranges") or []:
                start_off = int(rng.get("startOffset", 0) or 0)
                end_off = int(rng.get("endOffset", 0) or 0)
                if end_off <= start_off:
                    continue
                group["total"] = max(group["total"], end_off)
                bucket = "used" if int(rng.get("count", 0) or 0) > 0 else "dead"
                group[bucket].append((start_off, end_off))

    scripts = []
    for key, group in groups.items():
        total = group["total"]
        # V8 reports a file-level range covering the whole script with
        # count>0 when the top-level block ran, plus nested function ranges
        # with count==0 for code that never executed. Dead zones must win,
        # otherwise every file looks 100% covered.
        covered = _merged_length(
            _subtract_ranges(group["used"], group["dead"])
        )
        name = group["url"].rsplit("/", 1)[-1] if group["url"] else key
        scripts.append({
            "name": name[:120],
            "url": group["url"][:200],
            "total_bytes": total,
            "used_bytes": min(covered, total),
            "pct": round(covered / total * 100, 1) if total else 0.0,
        })
    scripts.sort(key=lambda s: s["used_bytes"] - s["total_bytes"])
    total_bytes = sum(s["total_bytes"] for s in scripts)
    used_bytes = sum(s["used_bytes"] for s in scripts)
    return {
        "supported": True,
        "scripts": scripts[:25],
        "script_count": len(scripts),
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "pct": round(used_bytes / total_bytes * 100, 1) if total_bytes else 0.0,
    }


def classify_flow_status(results: list[dict]) -> str:
    """Return the user-facing run status while preserving step-level detail."""
    statuses = {result.get("status") for result in results}
    if not statuses or statuses <= {"passed"}:
        return "passed"
    if "inconclusive" in statuses:
        return "inconclusive"
    if "blocked" in statuses:
        return "blocked"
    return "failed"


def summarize_flow_diagnostics(results: list[dict], telemetry: dict) -> dict:
    """Build a compact, machine-readable diagnosis for a flow report."""
    failures = [r for r in results if r.get("status") != "passed"]
    categories: dict[str, int] = {}
    for result in failures:
        reason = str(result.get("reason") or result.get("status") or "failed")
        categories[reason] = categories.get(reason, 0) + 1
    diagnostics = []
    for result in failures[:20]:
        diagnostics.append({
            "step": result.get("i"),
            "action": result.get("action"),
            "status": result.get("status"),
            "reason": result.get("reason", ""),
            "error": result.get("error", ""),
            "cause": result.get("cause", {}),
        })
    if telemetry.get("console_errors"):
        diagnostics.append({
            "kind": "console",
            "count": len(telemetry.get("console_errors") or []),
            "samples": list(telemetry.get("console_errors") or [])[:5],
        })
    if telemetry.get("failed_requests"):
        diagnostics.append({
            "kind": "network",
            "count": len(telemetry.get("failed_requests") or []),
            "samples": list(telemetry.get("failed_requests") or [])[:5],
        })
    if telemetry.get("bad_responses"):
        diagnostics.append({
            "kind": "http",
            "count": len(telemetry.get("bad_responses") or []),
            "samples": list(telemetry.get("bad_responses") or [])[:5],
        })
    if telemetry.get("dialogs"):
        diagnostics.append({
            "kind": "dialog",
            "count": len(telemetry.get("dialogs") or []),
            "samples": [
                f"{d.get('type')}:{d.get('message', '')[:80]}"
                f"{'(accepted)' if d.get('accepted') else '(dismissed)'}"
                for d in list(telemetry.get("dialogs") or [])[:5]
            ],
        })
    return {
        "failed_steps": len(failures),
        "categories": categories,
        "items": diagnostics,
    }

def normalize_flow_steps(steps) -> list[dict]:
    """Accept a JSON string, a ``{"steps": [...]}`` wrapper, or a list.

    Bare strings become navigate steps (``["https://x", ...]``), which keeps
    the common smoke-test case terse.
    """
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError as exc:
            raise SessionRefused("invalid_flow", f"steps is not valid JSON: {exc}") from exc
    if isinstance(steps, dict):
        steps = steps.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SessionRefused("invalid_flow", "steps must be a non-empty list")
    if len(steps) > MAX_FLOW_STEPS:
        raise SessionRefused("flow_too_long", f"max {MAX_FLOW_STEPS} steps per flow")
    out: list[dict] = []
    for step in steps:
        if isinstance(step, dict):
            out.append(step)
        elif isinstance(step, str):
            out.append({"action": "navigate", "url": step})
        else:
            raise SessionRefused("invalid_step", f"unsupported step: {step!r}")
    return out


def parse_dialog_spec(spec: Any) -> tuple[bool, str]:
    """Normalise a dialog answer spec to ``(accept, prompt_text)``.

    Accepts ``"accept"`` / ``"dismiss"`` / ``true`` / ``{"accept": false}`` so
    both the JSON flow and the MCP string args read naturally.
    """
    if isinstance(spec, dict):
        raw = spec.get("accept", spec.get("action", True))
        text = str(spec.get("text", spec.get("prompt_text", "")) or "")
    else:
        raw, text = spec, ""
    if isinstance(raw, str):
        verb = raw.strip()
        # "accept:my answer" answers prompt() without the object form.
        if ":" in verb and verb.split(":", 1)[0].strip().lower() in (
            "accept", "dismiss", "ok", "no", "cancel"
        ):
            verb, _, text = verb.partition(":")
        accept = verb.strip().lower() in ("accept", "ok", "yes", "true", "1", "y")
    else:
        accept = bool(raw)
    return accept, text


def normalize_acts(actions: Any) -> list[dict]:
    """Accept a JSON string, an ``{"actions": [...]}`` wrapper, or a list."""
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except json.JSONDecodeError as exc:
            raise SessionRefused("invalid_step", f"actions is not valid JSON: {exc}") from exc
    if isinstance(actions, dict):
        actions = actions.get("actions") or actions.get("steps")
    if not isinstance(actions, list) or not actions:
        raise SessionRefused("invalid_step", "actions must be a non-empty list")
    if len(actions) > MAX_BATCH_ACTS:
        raise SessionRefused(
            "invalid_step", f"at most {MAX_BATCH_ACTS} acts per batch",
        )
    out: list[dict] = []
    for item in actions:
        if isinstance(item, dict):
            out.append(item)
        else:
            raise SessionRefused("invalid_step", f"unsupported act: {item!r}")
    return out


def render_flow_report(report: dict, *, max_errors: int = 5) -> str:
    """Render a flow report as a compact, token-lean Markdown digest."""
    icon = str(report.get("status") or ("PASS" if report.get("ok") else "FAIL")).upper()
    head = (
        f"# Browser test {icon} — {report.get('passed', 0)}/{report.get('total', 0)} steps "
        f"in {report.get('duration_ms', 0)}ms\n"
        f"final: {report.get('title', '') or '(untitled)'} — {report.get('final_url', '')}\n"
        f"~{report.get('tokens_estimate', estimate_tokens(report))} tokens"
        f"{' · uses JS evaluate' if report.get('uses_evaluate') else ''}"
    )
    savings = report.get("savings") or {}
    if savings.get("saved_tokens"):
        head += (
            f"\nsaved ~{savings['saved_tokens']} tokens and "
            f"{savings.get('calls_avoided', 0)} tool calls vs a snapshot/act loop"
        )
    lines = [head, ""]
    for step in report.get("steps") or []:
        status = step.get("status") or ("passed" if step.get("ok", True) else "failed")
        mark = {"passed": "ok", "blocked": "BLOCK", "inconclusive": "INCONCLUSIVE"}.get(
            str(status), "FAIL",
        )
        bit = f"{mark} #{step.get('i')} {step.get('action')}"
        if step.get("detail"):
            bit += f" — {step['detail']}"
        if status != "passed":
            bit += f" — {step.get('reason')}: {step.get('error')}"
        lines.append(bit)
        for cand in step.get("candidates") or []:
            lines.append(f"      candidate {cand.get('ref')}: {cand.get('name')}")
        cause = step.get("cause") or {}
        cause_bits = []
        if cause.get("dom"):
            d = cause["dom"]
            cause_bits.append(f"dom +{d.get('added', 0)}/-{d.get('removed', 0)}/~{d.get('changed', 0)}")
        if cause.get("failed_requests"):
            cause_bits.append(f"{len(cause['failed_requests'])} failed req")
        if cause.get("console_errors"):
            cause_bits.append(f"{len(cause['console_errors'])} console error")
        if cause_bits:
            lines.append(f"      cause: {'; '.join(cause_bits)}")

    for item in (report.get("console_errors_source") or [])[:max_errors]:
        if item.get("source"):
            lines.append(f"console {item['source']}:{item.get('source_line')} — {item.get('text', '')[:120]}")

    errors = report.get("console_errors") or []
    if errors:
        lines.append(f"\nconsole errors ({len(errors)}):")
        lines.extend(f"  - {e[:160]}" for e in errors[:max_errors])
    failed_reqs = report.get("failed_requests") or []
    if failed_reqs:
        lines.append(f"\nfailed requests ({len(failed_reqs)}):")
        lines.extend(
            f"  - {r.get('method')} {r.get('url')[:120]} — {r.get('failure')[:80]}"
            for r in failed_reqs[:max_errors]
        )
    bad = report.get("bad_responses") or []
    if bad:
        lines.append(f"\nHTTP >=400 ({len(bad)}):")
        lines.extend(f"  - {r.get('status')} {r.get('method')} {r.get('url')[:120]}" for r in bad[:max_errors])
    dialogs = report.get("dialogs") or []
    if dialogs:
        lines.append(f"\ndialogs ({len(dialogs)}):")
        lines.extend(
            f"  - {d.get('type')} {str(d.get('message', ''))[:100]} "
            f"{'accepted' if d.get('accepted') else 'dismissed'}"
            for d in dialogs[:max_errors]
        )
    artifacts = report.get("artifacts") or {}
    if artifacts:
        lines.append("\nartifacts: " + ", ".join(f"{k}={v}" for k, v in artifacts.items()))
    return "\n".join(lines)


class BrowserAgentSession:
    """One agent-facing session: catalog snapshots, gated actions, receipts."""

    def __init__(
        self,
        session_id: str,
        page: PageAdapter,
        *,
        allow_domains: list[str],
        require_approval: bool = True,
        scrub_pii: bool = True,
        allow_private: bool = False,
        created_at: Optional[float] = None,
        artifacts_dir: Optional[str] = None,
    ):
        if not allow_domains:
            raise ValueError("allow_domains must be non-empty (fail closed)")
        self.id = session_id
        self.page = page
        self.network_policy = getattr(page, "network_policy", None)
        self.allow_domains = [d.strip() for d in allow_domains if d.strip()]
        self.require_approval = require_approval
        self.scrub_pii = scrub_pii
        # Local dev testing: permit loopback/private hosts, but only for hosts
        # that also appear in the explicit allowlist (both gates must agree).
        self.allow_private = allow_private
        self.created_at = created_at or time.time()
        self.steps: list[Step] = []
        self.catalog: dict[str, dict] = {}
        self.last_url = ""
        self.closed = False
        self._source_maps: dict[str, Optional[SourceMapData]] = {}
        # Human-in-the-loop hook: interactive sessions can pause for a human
        # (CAPTCHA/2FA) and resume. The desktop UI drives this; the backend
        # exposes the state and frame.
        self.human_takeover = False
        # Action recording: when on, performed actions are captured as a flow.
        self.recording = False
        self.recorded_steps: list[dict] = []
        self._settle_ms = 0
        self._forbid_evaluate = False
        # Last API-step response, read by assert_status/json/latency/schema.
        self._last_api: Optional[dict] = None
        # Where saved downloads land (artifact dir when the session has one).
        self.artifacts_dir = artifacts_dir

    # -- helpers -------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        if not self.scrub_pii or not text:
            return text
        from backend.core.privacy import PIIDetector

        masked = text
        for pii_type in PIIDetector.PATTERNS:
            masked = PIIDetector.mask_pii(masked, pii_type)
        return masked

    def _record(self, action: str, outcome: str, *, url: str = "",
                ref: Optional[str] = None, detail: str = "") -> Step:
        prev = self.steps[-1].step_hash if self.steps else None
        step = Step(
            seq=len(self.steps) + 1, ts=time.time(), action=action,
            outcome=outcome, url=url or self.last_url, ref=ref, detail=detail,
            prev_hash=prev,
        )
        payload = {
            "seq": step.seq, "ts": step.ts, "action": step.action,
            "outcome": step.outcome, "url": step.url, "ref": step.ref,
            "detail": step.detail, "prev_hash": step.prev_hash,
        }
        step.step_hash = hashlib.sha256(js_dumps(payload).encode("utf-8")).hexdigest()
        self.steps.append(step)
        if len(self.steps) > MAX_STEPS:
            self.steps = self.steps[-MAX_STEPS:]
        return step

    def _require_agent_control(self, action: str) -> None:
        """Refuse mutating actions while a human has taken over the session."""
        if self.human_takeover:
            raise SessionRefused(
                "human_takeover",
                f"session is under human control; '{action}' refused",
            )

    def _check_navigation(self, url: str) -> None:
        if not is_safe_url(url, allow_private=self.allow_private):
            raise SessionRefused("unsafe_url", f"URL failed safety checks: {url}")
        if not host_allowed(_host(url), self.allow_domains):
            raise SessionRefused(
                "blocked_domain",
                f"{_host(url) or url} is outside the allowlist: {self.allow_domains}",
            )

    def _guard_click_target(self, ref: str) -> dict:
        element = self.catalog.get(ref)
        if element is None:
            raise SessionRefused(
                "unknown_ref", f"{ref} is not in the current snapshot — snapshot again",
            )
        href = element.get("href") or ""
        if href and href.startswith("http"):
            host = _host(href)
            if host and not host_allowed(host, self.allow_domains):
                raise SessionRefused(
                    "blocked_domain",
                    f"link target {host} is outside the allowlist: {self.allow_domains}",
                )
        return element

    # -- API -----------------------------------------------------------------

    @property
    def download_dir(self) -> str:
        """Directory saved downloads land in (created on the first download)."""
        return download_dir_for(self.id, self.artifacts_dir)

    def info(self) -> dict:
        return {
            "session_id": self.id,
            "allow_domains": self.allow_domains,
            "require_approval": self.require_approval,
            "scrub_pii": self.scrub_pii,
            "allow_private": self.allow_private,
            "network_policy": (
                self.network_policy.report() if self.network_policy is not None
                else {"enforced": False, "reason": "adapter_without_request_policy"}
            ),
            "created_at": self.created_at,
            "age_seconds": round(time.time() - self.created_at, 1),
            "steps": len(self.steps),
            "url": self.last_url,
            "closed": self.closed,
        }

    async def navigate(self, url: str) -> dict:
        try:
            self._check_navigation(url)
        except SessionRefused as refusal:
            self._record("navigate", "blocked", url=url, detail=refusal.detail)
            raise
        await self.page.goto(url)
        self.last_url = await self.page.current_url()
        # Post-check: redirects can land outside the allowlist.
        if not host_allowed(_host(self.last_url), self.allow_domains):
            await self.page.goto("about:blank")
            self._record(
                "navigate", "reverted", url=self.last_url,
                detail=f"redirected outside allowlist: {_host(self.last_url)}",
            )
            raise SessionRefused(
                "blocked_domain",
                f"redirect landed on {_host(self.last_url)} (outside allowlist); reverted",
            )
        step = self._record("navigate", "ok", url=self.last_url)
        self._capture_step({"action": "navigate", "url": url})
        return {"url": self.last_url, "step": step.to_dict()}

    async def _read_state(self) -> dict:
        """Fetch + scrub the current page state and refresh the catalog.

        Does *not* append a receipt — internal re-observations between flow
        steps should not pollute the audited step log.
        """
        raw = await self.page.snapshot()
        elements = []
        for element in (raw.get("elements") or [])[:MAX_ELEMENTS]:
            elements.append({
                "ref": element.get("ref"),
                "tag": element.get("tag"),
                "role": element.get("role"),
                "type": element.get("type"),
                "name": self._scrub(element.get("name") or ""),
                "href": self._scrub(element.get("href") or ""),
                "value": self._scrub(element.get("value") or ""),
                "visible": element.get("visible", True),
                "disabled": bool(element.get("disabled", False)),
                "checked": element.get("checked"),
                # Only present when the element is not in the top document:
                # an agent must know a ref lives in an iframe (or a shadow root)
                # before it decides a click "did nothing".
                **({"frame": element["frame"], "frame_url": element.get("frame_url", "")}
                   if element.get("frame") else {}),
                **({"shadow": True} if element.get("shadow") else {}),
                "risk": classify_risk(
                    element.get("name") or "", element.get("href") or "",
                ),
            })
        self.catalog = {e["ref"]: e for e in elements if e.get("ref")}
        text = self._scrub((raw.get("text") or "")[:MAX_TEXT_CHARS])
        self.last_url = raw.get("url") or self.last_url
        state = {
            "url": self.last_url,
            "title": self._scrub(raw.get("title") or ""),
            "text": text,
            "elements": elements,
            "count": len(elements),
        }
        if raw.get("frames"):
            state["frames"] = raw["frames"]
        return state

    async def snapshot(self, *, compact: bool = False, delta: bool = False,
                       observe: Optional[dict] = None) -> dict:
        """Perceive the page, optionally projected down to a token budget.

        The full state is what ``act`` resolves refs against and what the
        evidence bundle records, so it stays the default. ``compact``
        returns the same page through :func:`compact_observation` — column
        rows instead of dicts, a relevance filter and a hard token ceiling
        — for a caller that only needs to *read* the page rather than act
        on every row. ``delta`` additionally reports only what moved since
        the previous observation, which is the question a caller actually
        has right after a mutating step.
        """
        previous = list(self.catalog.values())
        state = await self._read_state()
        self._record("snapshot", "ok", url=self.last_url,
                     detail=f"{state['count']} elements")
        if not compact and not delta:
            return state
        options = dict(observe or {})
        unknown = sorted(set(options) - _OBSERVE_OPTIONS)
        if unknown:
            raise SessionRefused(
                "invalid_observation",
                f"unknown observe option(s): {', '.join(unknown)}; "
                f"expected any of {', '.join(sorted(_OBSERVE_OPTIONS))}",
            )
        if delta:
            # An absent previous catalog must stay None. Diffing against an
            # empty list would report the whole page as "added" instead of
            # listing its rows, which is the opposite of a delta.
            options.setdefault("changed_since", previous or None)
        return compact_observation(state, **options)

    async def act(self, action: str, ref: str, *, text: str = "",
                  approve: bool = False, selector: str = "",
                  files: Optional[list] = None, dialog: Any = "") -> dict:
        """One primitive action, with the rails.

        ``dialog`` answers the native dialog this action raises (``"accept"`` /
        ``"dismiss"`` / ``{"accept": true, "text": "…"}`` for ``prompt()``),
        which is what makes a ``confirm()``-guarded button testable in one call
        instead of an arm-then-click race.
        """
        if action not in _ACT_ACTIONS:
            raise SessionRefused("unknown_action", f"unsupported action: {action}")
        self._require_agent_control(action)
        if dialog:
            await self.arm_dialog(dialog)
        if action == "upload":
            return await self.upload_files(
                ref, files or [], approve=approve, selector=selector,
            )
        if selector and not ref:
            return await self.act_selector(action, selector, text=text, approve=approve)

        element = self.catalog.get(ref)
        if element is None:
            raise SessionRefused(
                "unknown_ref", f"{ref} is not in the current snapshot — snapshot again",
            )
        risk = classify_risk(element.get("name") or "", element.get("href") or "")
        if risk and not approve:
            self._record(action, "blocked", ref=ref,
                         detail=f"approval required (risky element: {risk!r})")
            raise SessionRefused(
                "approval_required",
                f"element looks irreversible ({risk!r}); re-send with approve=true",
            )
        if self.require_approval and not approve:
            self._record(action, "blocked", ref=ref, detail="session requires approval")
            raise SessionRefused(
                "approval_required", "session requires approve=true for input actions",
            )

        if action == "click":
            self._guard_click_target(ref)  # may raise blocked_domain / unknown_ref

        before_url = self.last_url
        if action == "click":
            await self.page.click(ref)
        else:
            await self.page.type_text(ref, text)
        await self._settle_navigation(action, ref, before_url)

        step = self._record(action, "ok", ref=ref,
                            detail=(text[:40] if action == "type" and text else ""))
        if action == "click":
            self._capture_step({"action": "click",
                                "target": element.get("name") or ref, "ref": ref})
        else:
            eltype = (element.get("type") or "").lower()
            name = (element.get("name") or "").lower()
            if eltype == "password" or "password" in name:
                recorded_value = "{{password}}"
            elif eltype == "email" or "email" in name:
                recorded_value = "{{email}}"
            else:
                recorded_value = self._scrub(text)
            self._capture_step({
                "action": "type", "target": element.get("name") or ref,
                "value": recorded_value,
            })
        return {"outcome": "ok", "url": self.last_url, "step": step.to_dict()}

    async def _settle_navigation(self, action: str, ref: str, before_url: str) -> None:
        """Post-action URL check: revert disallowed landings, else adopt."""
        after_url = await self.page.current_url()
        if after_url == before_url:
            return
        if not host_allowed(_host(after_url), self.allow_domains):
            await self.page.goto("about:blank")
            self.last_url = "about:blank"
            self._record(action, "reverted", ref=ref, url=after_url,
                         detail=f"navigation to {_host(after_url)} blocked; reverted")
            raise SessionRefused(
                "blocked_domain",
                f"action navigated to {_host(after_url)} (outside allowlist); reverted",
            )
        self.last_url = after_url

    async def act_selector(self, action: str, selector: str, *, text: str = "",
                           approve: bool = False) -> dict:
        """Dispatch click/type by CSS/XPath selector (bypasses the catalog).

        Selectors address elements the snapshot never catalogs, so the risk
        classifier cannot see them — they always require explicit approval.
        """
        if action not in ("click", "type"):
            raise SessionRefused("unknown_action", f"unsupported action: {action}")
        self._require_agent_control(action)
        if not (selector or "").strip():
            raise SessionRefused("invalid_step", "selector is empty")
        if not approve:
            self._record(action, "blocked", detail="selector actions require approve=true")
            raise SessionRefused(
                "approval_required",
                "selector actions address unclassified elements; re-send with approve=true",
            )
        if not self.last_url:
            try:
                self.last_url = await self.page.current_url()
            except Exception:
                # A page that cannot report its URL (closed/crashed) is
                # handled by the step itself; keep going.
                log.debug("current_url unavailable; using last known url",
                          exc_info=True)
        before_url = self.last_url
        if action == "click":
            await self._call_optional("click_selector", selector)
        else:
            await self._call_optional("fill_selector", selector, text)
        await self._settle_navigation(action, None, before_url)
        step = self._record(action, "ok",
                            detail=(f"{selector[:60]} ← {text[:40]}"
                                    if action == "type" and text else selector[:60]))
        self._capture_step(
            {"action": action, "selector": selector, **({"value": self._scrub(text)} if action == "type" else {})}
        )
        return {"outcome": "ok", "url": self.last_url, "step": step.to_dict()}

    # -- dialogs --------------------------------------------------------------

    async def arm_dialog(self, spec: Any) -> dict:
        """Decide how the *next* dialog raised by an action is answered.

        Nothing is armed by default, which means dialogs get dismissed (the
        Playwright default) and still land in telemetry — an agent that ignores
        ``confirm()`` learns it changed nothing instead of guessing.
        """
        accept, text = parse_dialog_spec(spec)
        await self._call_optional("arm_dialog", accept, text)
        self._record("dialog", "ok", detail=f"armed {'accept' if accept else 'dismiss'}")
        return {"armed": "accept" if accept else "dismiss",
                **({"text": text} if text else {})}

    # -- uploads --------------------------------------------------------------

    async def upload_files(self, ref: str, files: Any, *, approve: bool = False,
                           selector: str = "", chooser: Optional[bool] = None) -> dict:
        """Attach local files to a file input, or feed the picker a button opens.

        Uploads read from the engine's own disk and the risk classifier cannot
        see file contents, so they always need ``approve=true`` plus a path that
        resolves inside the configured upload roots.
        """
        self._require_agent_control("upload")
        if not approve:
            self._record("upload", "blocked", ref=ref or None,
                         detail="uploads require approve=true")
            raise SessionRefused(
                "approval_required",
                "upload reads local files; re-send with approve=true",
            )
        paths = resolve_upload_paths(files)
        element = self.catalog.get(ref) if ref else None
        if element is None and not selector:
            raise SessionRefused("target_required", "upload needs a 'ref' or 'selector'")
        wants_input = chooser is False or (
            chooser is None and element is not None
            and (element.get("tag") or "").lower() == "input"
            and (element.get("type") or "").lower() == "file"
        )
        if selector and not ref:
            info = await self._call_optional("set_input_files_selector", selector, paths)
        elif wants_input:
            info = await self._call_optional("set_input_files", ref, paths)
        else:
            info = await self._call_optional("upload_via_chooser", ref, paths)
        info = info or {}
        names = [os.path.basename(p) for p in paths]
        mode = str(info.get("mode") or ("input" if wants_input else "chooser"))
        step = self._record("upload", "ok", ref=ref or None,
                            detail=f"{len(paths)} file(s) via {mode}")
        self._capture_step({
            "action": "upload",
            "target": (element or {}).get("name") or ref or selector,
            "files": names,
            **({"chooser": True} if mode == "chooser" else {}),
        })
        return {"outcome": "ok", "url": self.last_url, "uploaded": names,
                "mode": mode, "step": step.to_dict()}

    # -- batched primitives ---------------------------------------------------

    async def act_many(self, actions: Any, *, approve: bool = False,
                       stop_on_error: bool = True) -> dict:
        """Run up to :data:`MAX_BATCH_ACTS` primitives in one call.

        The primitive loop's cost is round trips, not the actions: N acts means
        N tool calls and, because refs go stale after any mutation, up to N
        re-observations. This keeps every per-action receipt and refusal (each
        act is still recorded and hash-chained) but pays one tool call, one
        response, and zero re-snapshots while refs stay valid. When the page
        navigates mid-batch, the result says ``reobserve: true`` so the agent
        re-observes once instead of guessing.
        """
        items = normalize_acts(actions)
        before_tel = self._telemetry_counts()
        before_url = self.last_url
        results: list[dict] = []
        failed = 0
        stopped_at: Optional[int] = None
        for index, item in enumerate(items):
            action = str(item.get("action") or "").strip().lower()
            ref = str(item.get("ref") or "")
            entry: dict = {"i": index, "action": action or "?"}
            if ref:
                entry["ref"] = ref
            wants_approve = bool(item.get("approve", approve))
            try:
                if action in _ACT_ACTIONS:
                    extra = await self.act(
                        action, ref,
                        text=str(item.get("text", item.get("value", "")) or ""),
                        approve=wants_approve,
                        selector=str(item.get("selector") or ""),
                        files=item.get("files"),
                        dialog=item.get("dialog", ""),
                    )
                else:
                    # Everything the flow runner understands (press, select,
                    # check, hover, wait, assert_*, navigate, download, …) is
                    # fair game in a batch too — with observe=False so the batch
                    # pays for one observation, not one per act.
                    summary, extra = await self._run_step(
                        dict(item), approve=wants_approve, observe=False,
                    )
                    entry["detail"] = summary
                entry["ok"] = True
                if extra.get("uploaded"):
                    entry["uploaded"] = extra["uploaded"]
                if extra.get("download"):
                    entry["download"] = extra["download"]
                if self.last_url != before_url:
                    entry["url"] = self.last_url
            except SessionRefused as refusal:
                entry["ok"] = False
                entry["reason"] = refusal.reason
                entry["error"] = (refusal.detail or refusal.reason)[:200]
                if refusal.candidates:
                    entry["candidates"] = refusal.candidates[:MAX_TARGET_CANDIDATES]
                failed += 1
            except Exception as exc:  # harness/page error, not a safety refusal
                entry["ok"] = False
                entry["reason"] = "harness_error"
                entry["error"] = str(exc)[:200]
                failed += 1
            results.append(entry)
            if failed and stop_on_error:
                stopped_at = index
                break
        deltas = {
            key: value - before_tel.get(key, 0)
            for key, value in self._telemetry_counts().items()
        }
        deltas = {key: value for key, value in deltas.items() if value}
        self._record(
            "act_batch", "ok" if not failed else "failed",
            detail=f"{len(results) - failed}/{len(items)} acts ok",
        )
        out: dict = {
            "ok": failed == 0,
            "count": len(results),
            "passed": len(results) - failed,
            "url": self.last_url,
            "results": results,
        }
        if stopped_at is not None:
            out["stopped_at"] = stopped_at
        if self.last_url != before_url:
            out["reobserve"] = True
        if deltas:
            out["telemetry"] = deltas
        return out

    def telemetry_report(self, *, drain: bool = False) -> dict:
        """Read standing console/network/dialog telemetry — no re-snapshot.

        Lets a primitive loop answer "did anything break?" for a few dozen
        tokens instead of re-snapshotting the page to find out.
        """
        data = self._drain_telemetry() if drain else self._peek_telemetry()
        data.setdefault("dialogs", [])
        out: dict = {
            "session_id": self.id, "url": self.last_url,
            "supported": bool(data), **data,
        }
        made = getattr(self.page, "requests", None)
        if isinstance(made, list):
            out["requests_made"] = len(made)
        if drain:
            self._record("telemetry", "ok", detail="drained")
        return out

    def resolve_target(self, target: str) -> str:
        """Resolve a ref or a human-readable target to a catalog ref.

        Accepts ``@e3`` refs, exact names, role-prefixed names
        ("button Sign in"), and unique substrings. Ambiguity raises with a
        bounded candidate list so the agent can retry in one fewer round trip.
        """
        if not target or not str(target).strip():
            raise SessionRefused("target_required", "step needs a 'ref' or 'target'")
        t = str(target).strip()
        if t in self.catalog:
            return t
        if t.startswith("@e"):
            raise SessionRefused(
                "unknown_ref", f"{t} is not in the current snapshot — snapshot again",
            )
        low = t.lower()

        def match(pred) -> list[str]:
            return [r for r, e in self.catalog.items() if pred(e)]

        exact = match(lambda e: (e.get("name") or "").strip().lower() == low)
        if len(exact) == 1:
            return exact[0]

        parts = low.split(None, 1)
        if len(parts) == 2 and parts[0] in {
            "button", "link", "input", "select", "textarea", "tab", "checkbox", "a",
        }:
            role, rest = parts
            role_hits = match(
                lambda e: (e.get("role") or e.get("tag") or "").lower() == role
                and rest in (e.get("name") or "").lower()
            )
            if len(role_hits) == 1:
                return role_hits[0]

        contains = match(lambda e: low in (e.get("name") or "").lower())
        if len(contains) == 1:
            return contains[0]

        pool = exact or contains
        candidates = [
            {
                "ref": r, "tag": self.catalog[r].get("tag"),
                "role": self.catalog[r].get("role"),
                "name": (self.catalog[r].get("name") or "")[:60],
            }
            for r in pool[:MAX_TARGET_CANDIDATES]
        ]
        if candidates:
            raise SessionRefused(
                "target_ambiguous",
                f"{target!r} matched {len(pool)} elements; use a ref",
                candidates=candidates,
            )
        raise SessionRefused(
            "target_not_found",
            f"no element matches {target!r}",
            candidates=[
                {"ref": r, "name": (e.get("name") or "")[:50]}
                for r, e in list(self.catalog.items())[:MAX_TARGET_CANDIDATES]
            ],
        )

    async def _target(self, step: dict) -> str:
        if not self.catalog:
            await self._read_state()
        target = step.get("ref") or step.get("target") or step.get("name") or ""
        return self.resolve_target(target)

    # -- optional adapter capabilities --------------------------------------

    async def _call_optional(self, name: str, *args, **kwargs):
        fn = getattr(self.page, name, None)
        if fn is None:
            raise SessionRefused(
                "unsupported_action", f"page adapter does not support {name!r}",
            )
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def _drain_telemetry(self) -> dict:
        drain = getattr(self.page, "drain_telemetry", None)
        if callable(drain):
            try:
                return drain() or {}
            except Exception:
                return {}
        return {}

    def _peek_telemetry(self) -> dict:
        peek = getattr(self.page, "peek_telemetry", None)
        if callable(peek):
            try:
                return peek() or {}
            except Exception:
                return {}
        return {}

    async def _safe_state(self) -> dict:
        try:
            return await self._read_state()
        except Exception:
            return {"url": self.last_url, "title": "", "text": "", "elements": [], "count": 0}

    # -- declarative flow runner --------------------------------------------

    async def run_flow(
        self, steps, *, approve: bool = False, stop_on_failure: bool = False,
        observe: bool = True, network: Optional[dict] = None,
        freeze_animations: bool = True, resolve_sources: bool = False,
        settle_ms: int = 0, forbid_evaluate: bool = False,
        clock: Optional[dict] = None, throttle: Optional[dict] = None,
        coverage: bool = False,
    ) -> dict:
        """Execute a declarative list of steps and return one compact report.

        This is the token-efficiency centrepiece: the agent describes the whole
        test once; the runner performs navigation, intent-based clicks/types,
        waits, assertions and re-observation internally, and hands back a
        pass/fail digest plus auto-collected console/network telemetry — no
        snapshot/act round trips per step.

        ``network`` installs request interception (mocks/fail/delay/offline),
        ``resolve_sources`` maps console errors through source maps, and each
        step carries the telemetry + DOM delta it caused.

        Determinism knobs (all optional, all reported back so a run states
        the conditions it executed under):

        ``clock``     — ``{"time": ..., "rate": 0}`` freezes/advances the
                        page clock (Playwright ``page.clock``).
        ``throttle``  — CDP network shaping (``{"offline": true}`` or
                        kbps/latency) so 3G / offline paths are testable.
        ``coverage``  — capture JS coverage and summarise used bytes per
                        script in the report (Chromium).
        """
        normalized = normalize_flow_steps(steps)
        results: list[dict] = []
        passed = failed = 0
        started = time.time()
        self._settle_ms = max(0, int(settle_ms or 0))
        self._forbid_evaluate = bool(forbid_evaluate)

        network_info: dict = {
            "rules": 0,
            "offline": False,
            "policy": bool(self.network_policy),
        }
        if network or self.network_policy is not None:
            network_info = await self._call_optional("setup_network", network or {}) or network_info
        determinism: dict = {}
        if clock:
            try:
                determinism["clock"] = await self._call_optional("install_clock", clock)
            except SessionRefused as exc:
                determinism["clock"] = {"skipped": exc.detail or "unsupported"}
        if throttle:
            try:
                determinism["throttle"] = await self._call_optional("set_throttle", throttle)
            except SessionRefused as exc:
                determinism["throttle"] = {"skipped": exc.detail or "unsupported"}
        coverage_info: dict = {}
        if coverage:
            try:
                await self._call_optional("start_coverage")
            except SessionRefused as exc:
                coverage_info = {"supported": False, "reason": exc.detail or "unsupported"}
        if freeze_animations:
            try:
                await self._call_optional("inject_css", DISABLE_ANIM_CSS)
            except SessionRefused:
                pass
        try:
            await self._call_optional("init_perf_observers")
        except SessionRefused:
            pass

        for i, step in enumerate(normalized, 1):
            t0 = time.time()
            action = (step.get("action") or "?").strip().lower()
            result: dict = {"i": i, "action": action, "status": "passed"}
            before_elements = list(self.catalog.values())
            before_tel = self._telemetry_counts()
            try:
                step_approve = bool(step.get("approve", approve))
                detail, evidence = await self._run_step(
                    step, approve=step_approve, observe=observe,
                )
                if detail:
                    result["detail"] = detail
                if evidence:
                    result.update(evidence)
                passed += 1
            except SessionRefused as refusal:
                result["status"] = classify_step_failure(refusal.reason)
                result["reason"] = refusal.reason
                result["error"] = refusal.detail or refusal.reason
                if refusal.candidates:
                    result["candidates"] = refusal.candidates
                failed += 1
            except Exception as exc:  # unexpected page/tool error
                result["status"] = "inconclusive"
                result["reason"] = "harness_error"
                result["error"] = str(exc)[:300]
                failed += 1

            # Cause attribution: what did this step change / emit?
            cause = self._attribute(before_elements, before_tel)
            if cause:
                result["cause"] = cause
            result["ms"] = int((time.time() - t0) * 1000)
            results.append(result)
            if result["status"] != "passed" and stop_on_failure:
                break

        telemetry = self._drain_telemetry()
        state = await self._safe_state()
        report = {
            "ok": failed == 0,
            "status": classify_flow_status(results),
            "passed": passed,
            "failed": failed,
            "total": len(results),
            "steps": results,
            "console_errors": telemetry.get("console_errors", []),
            "console_warnings": telemetry.get("console_warnings", []),
            "failed_requests": telemetry.get("failed_requests", []),
            "bad_responses": telemetry.get("bad_responses", []),
            "dialogs": telemetry.get("dialogs", []),
            "final_url": state.get("url", self.last_url),
            "title": state.get("title", ""),
            "duration_ms": int((time.time() - started) * 1000),
        }
        if network:
            report["network"] = network_info
        if determinism:
            report["determinism"] = determinism
        if coverage:
            try:
                coverage_info = await self._call_optional("stop_coverage") or {}
            except SessionRefused as exc:
                coverage_info = {"supported": False, "reason": exc.detail or "unsupported"}
        if coverage_info:
            report["coverage"] = coverage_info
        report["network_policy"] = (
            self.network_policy.report() if self.network_policy is not None
            else {"enforced": False, "reason": "adapter_without_request_policy"}
        )
        report["diagnostics"] = summarize_flow_diagnostics(results, telemetry)
        if resolve_sources:
            report["console_errors_source"] = await self._resolve_sources(
                telemetry.get("console_errors_detail") or [],
            )
        report["uses_evaluate"] = any(
            (s.get("action") or "").lower() == "evaluate" for s in normalized
        )
        report["tokens_estimate"] = estimate_tokens(report)
        # The answer to "is this actually cheaper?": the same script driven as
        # MCP primitives costs one call per action plus a fresh page state
        # after every mutation that moves the DOM.
        primitive = estimate_primitive_cost(
            normalized,
            elements=len(self.catalog) or int(state.get("count") or 0),
            text_chars=len(str(state.get("text") or "")),
        )
        report["savings"] = {
            "steps": len(normalized),
            "flow_calls": 1,
            "primitive_calls": primitive["calls"],
            "calls_avoided": max(0, primitive["calls"] - 1),
            "flow_tokens": report["tokens_estimate"],
            "primitive_tokens": primitive["tokens"],
            "saved_tokens": max(0, primitive["tokens"] - report["tokens_estimate"]),
        }
        record_flow_savings(report["savings"])
        self._record(
            "run_flow", "ok" if report["ok"] else "failed",
            detail=f"{passed}/{len(results)} steps passed",
        )
        return report

    def _telemetry_counts(self) -> dict:
        t = self._peek_telemetry()
        return {
            "console_errors": len(t.get("console_errors") or []),
            "failed_requests": len(t.get("failed_requests") or []),
            "bad_responses": len(t.get("bad_responses") or []),
            "dialogs": len(t.get("dialogs") or []),
        }

    def _attribute(self, before_elements: list[dict], before_tel: dict) -> dict:
        """Summarise the telemetry + DOM changes a step caused."""
        cause: dict = {}
        tel = self._peek_telemetry()
        new_errors = (tel.get("console_errors") or [])[before_tel["console_errors"]:]
        new_failed = (tel.get("failed_requests") or [])[before_tel["failed_requests"]:]
        new_bad = (tel.get("bad_responses") or [])[before_tel["bad_responses"]:]
        new_dialogs = (tel.get("dialogs") or [])[before_tel.get("dialogs", 0):]
        if new_errors:
            cause["console_errors"] = [e[:160] for e in new_errors[:3]]
        if new_failed:
            cause["failed_requests"] = new_failed[:3]
        if new_bad:
            cause["bad_responses"] = new_bad[:3]
        if new_dialogs:
            cause["dialogs"] = [
                f"{d.get('type')}:{d.get('message', '')[:60]}"
                f"{'(accepted)' if d.get('accepted') else '(dismissed)'}"
                for d in new_dialogs[:3]
            ]
        dom = diff_elements(before_elements, list(self.catalog.values()))
        dom_small = {k: dom[k] for k in ("added", "removed", "changed") if dom.get(k)}
        if dom_small:
            dom_small["added_names"] = dom["added_names"]
            dom_small["removed_names"] = dom["removed_names"]
            cause["dom"] = dom_small
        return cause

    async def _resolve_sources(self, detail: list[dict]) -> list[dict]:
        """Map console-error locations through the page's source maps."""
        out: list[dict] = []
        for item in detail:
            location = item.get("location") or {}
            resolved = None
            url = location.get("url") or ""
            if url.startswith(("http://", "https://")):
                data = await self._get_source_map(url)
                if data is not None:
                    try:
                        resolved = data.lookup(
                            int(location.get("lineNumber", 0)) + 1,
                            int(location.get("columnNumber", 0)),
                        )
                    except Exception:
                        resolved = None
            entry = {"text": item.get("text", "")[:200], "location": location}
            if resolved:
                entry["source"] = resolved.get("source")
                entry["source_line"] = resolved.get("line")
            out.append(entry)
        return out

    async def _get_source_map(self, script_url: str) -> Optional[SourceMapData]:
        map_url = map_url_for(script_url)
        if map_url in self._source_maps:
            return self._source_maps[map_url]
        data: Optional[SourceMapData] = None
        try:
            raw = await self._call_optional("fetch_text", map_url)
            if raw:
                import json

                data = SourceMapData(json.loads(raw))
        except Exception:
            data = None
        self._source_maps[map_url] = data
        return data

    async def _run_step(self, step: dict, *, approve: bool, observe: bool):
        action = (step.get("action") or "").strip().lower()
        if not action:
            raise SessionRefused("invalid_step", "step is missing 'action'")
        if action in _MUTATING_ACTIONS:
            self._require_agent_control(action)
        timeout = int(step.get("timeout", DEFAULT_STEP_TIMEOUT_MS))

        # A step can answer the dialog *it* raises. Arming has to happen before
        # the dispatch, and the answer is one-shot so it cannot leak downstream.
        if step.get("dialog") and action != "dialog":
            await self.arm_dialog(step["dialog"])

        result = await run_dialog_and_navigation(self, action, step, observe)
        if result is not None:
            return result
        result = await run_interaction(self, action, step, approve, observe)
        if result is not None:
            return result
        result = await run_api_step(self, action, step, approve)
        if result is not None:
            return result
        result = await run_file_step(self, action, step, approve, observe)
        if result is not None:
            return result

        if action == "evaluate":
            script = step.get("script") or step.get("value") or ""
            if not script.strip():
                raise SessionRefused("invalid_step", "evaluate requires 'script'")
            if self._forbid_evaluate:
                raise SessionRefused(
                    "evaluate_forbidden",
                    "this flow forbids JS-dependent steps (forbid_evaluate)",
                )
            if not approve:
                raise SessionRefused(
                    "approval_required",
                    "evaluate runs arbitrary JS; re-send with approve=true",
                )
            result = await self._call_optional("eval_js", script)
            if observe:
                await self._read_state()
            text = self._scrub(str(result if result is not None else ""))[:MAX_ASSERT_TEXT]
            self._capture_step({"action": "evaluate", "script": script[:200]})
            return "evaluated", {"evaluated": text}

        if action in ("wait", "wait_for"):
            await self._run_wait(step, timeout, approve)
            await self._read_state()
            return "waited", {}

        if action == "screenshot":
            b64 = await self._call_optional("screenshot", bool(step.get("full_page", False)))
            return "screenshot captured", {
                "screenshot_base64": b64,
                "screenshot_bytes": len(b64) * 3 // 4,
            }

        if action == "assert" or action.startswith("assert_"):
            state = await self._read_state()
            # The assertion vocabulary lives in browser_assertions; this class
            # keeps only the plumbing (read state, raise on failure).
            from backend.modules.browser_assertions import evaluate_assert

            passed, message = await evaluate_assert(self, step, state)
            if not passed:
                raise SessionRefused("assertion_failed", message)
            return message, {}

        raise SessionRefused("unknown_action", f"unsupported action: {action}")

    async def _run_wait(self, step: dict, timeout: int, approve: bool = False) -> None:
        if step.get("js"):
            # A JS predicate is the escape hatch for "wait until the app is
            # actually ready" (a flag, a promise, a component state) — but it
            # runs script, so it carries evaluate's approval gate.
            if not approve:
                raise SessionRefused(
                    "approval_required",
                    "wait with a 'js' predicate runs script on the page; "
                    "re-send with approve=true",
                )
            await self._call_optional("wait_for_function", str(step["js"]), timeout)
            return
        if step.get("selector"):
            await self._call_optional("wait_for_selector", step["selector"], timeout)
            return
        if step.get("text"):
            await self._call_optional("wait_for_text", step["text"], timeout)
            return
        if step.get("url_contains"):
            deadline = time.time() + timeout / 1000
            while time.time() < deadline:
                if step["url_contains"] in await self.page.current_url():
                    return
                await asyncio.sleep(0.1)
            raise SessionRefused(
                "wait_timeout", f"url never contained {step['url_contains']!r}",
            )
        await self._call_optional("wait_for", timeout_ms=timeout)

    async def capture_screenshot(self, full_page: bool = False) -> Optional[str]:
        """Current frame as base64 PNG (live-view / takeover source)."""
        try:
            return await self._call_optional("screenshot", full_page)
        except SessionRefused:
            return None

    # -- action recording ----------------------------------------------------

    def start_recording(self) -> dict:
        self.recording = True
        self.recorded_steps = []
        return {"recording": True, "steps": 0}

    def stop_recording(self) -> dict:
        self.recording = False
        return {"recording": False, "steps": self.recorded_steps,
                "count": len(self.recorded_steps)}

    def _capture_step(self, step: dict) -> None:
        if self.recording:
            self.recorded_steps.append(step)

    async def _wait_network_quiet(self, quiet_ms: int = 600,
                                  timeout_ms: int = 15000) -> None:
        """Wait until the resource count stops changing for ``quiet_ms``.

        This is the hot-reload settle: a dev server injecting HMR modules
        keeps adding resources; waiting for quiet avoids reading a
        half-rebuilt page.
        """
        deadline = time.time() + timeout_ms / 1000
        last = -1
        stable_since = time.time()
        while time.time() < deadline:
            try:
                count = await self._call_optional("resource_count")
            except SessionRefused:
                return
            if count != last:
                last = count
                stable_since = time.time()
            elif (time.time() - stable_since) * 1000 >= quiet_ms:
                return
            await asyncio.sleep(0.1)

    def receipts(self) -> dict:
        hashes = [s.step_hash for s in self.steps if s.step_hash]
        return {
            "session_id": self.id,
            "steps": [s.to_dict() for s in self.steps],
            "count": len(self.steps),
            "merkle_root": merkle_root(hashes),
            "chain_head": hashes[-1] if hashes else None,
            "allow_domains": self.allow_domains,
            "human_takeover": self.human_takeover,
        }


# ---------------------------------------------------------------------------
# Service (session lifecycle)
# ---------------------------------------------------------------------------

class BrowserAgentService:
    """Manages agent sessions with caps, TTL, and real-browser creation."""

    def __init__(self, max_sessions: int = MAX_SESSIONS,
                 ttl_seconds: int = SESSION_TTL_SECONDS):
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, BrowserAgentSession] = {}
        self._browser_sessions: dict[str, Any] = {}
        self._artifacts: dict[str, dict] = {}

    def _prune(self) -> None:
        now = time.time()
        for sid in list(self._sessions):
            session = self._sessions[sid]
            if session.closed or now - session.created_at > self.ttl_seconds:
                self._sessions.pop(sid, None)
                self._browser_sessions.pop(sid, None)
                self._artifacts.pop(sid, None)

    async def open(
        self, *, allow_domains: list[str], require_approval: bool = True,
        scrub_pii: bool = True, privacy_level: Optional[str] = None,
        allow_private: bool = False, storage_state: Any = None,
        context_options: Optional[dict] = None, trace: bool = False,
        har: bool = False, video: bool = False,
        artifacts_dir: Optional[str] = None,
    ) -> BrowserAgentSession:
        self._prune()
        if len(self._sessions) >= self.max_sessions:
            raise SessionRefused(
                "session_limit",
                f"max {self.max_sessions} concurrent sessions — close one first",
            )
        session_id = f"bs-{uuid.uuid4().hex[:10]}"

        from backend.modules.browser import (
            BrowserManager, PrivacyLevel, SessionMode,
        )

        opts = dict(context_options or {})
        if storage_state:
            opts["storage_state"] = storage_state
        # Prevent service workers from creating an out-of-band network path.
        opts.setdefault("service_workers", "block")
        # Downloads are a first-class flow step ("export the CSV, then assert on
        # it"), so the context has to be allowed to accept them in the first place.
        opts.setdefault("accept_downloads", True)
        if (trace or har or video) and not artifacts_dir:
            artifacts_dir = tempfile.mkdtemp(prefix="jambu-artifacts-")
        if artifacts_dir:
            os.makedirs(artifacts_dir, exist_ok=True)
            if har:
                opts["record_har_path"] = os.path.join(artifacts_dir, "network.har")
            if video:
                opts["record_video_dir"] = artifacts_dir

        privacy = PrivacyLevel[privacy_level.upper()] if privacy_level else PrivacyLevel.ENHANCED
        browser_session = await BrowserManager.get_instance().get_session(
            session_id, mode=SessionMode.EPHEMERAL, privacy_level=privacy,
            context_options=opts or None,
        )
        page = await browser_session.get_page()
        if trace:
            starter = getattr(browser_session, "start_trace", None)
            if starter is not None:
                try:
                    await starter()
                except Exception:
                    log.warning("failed to start trace for %s", session_id, exc_info=True)
        policy = NetworkPolicy(allow_domains, allow_private=allow_private)
        adapter = PlaywrightPage(page, network_policy=policy)
        try:
            network_info = await adapter.setup_network({})
            if callable(getattr(page, "route", None)):
                if not network_info.get("supported"):
                    raise SessionRefused(
                        "network_policy_unavailable",
                        "Playwright request routing could not be installed; session refused",
                    )
                if not network_info.get("websocket_supported"):
                    raise SessionRefused(
                        "network_policy_unavailable",
                        "Playwright WebSocket routing is unavailable; session refused",
                    )
        except Exception:
            try:
                await browser_session.stop()
            except Exception:
                log.warning("failed to close browser after policy setup failure",
                            exc_info=True)
            raise
        agent = BrowserAgentSession(
            session_id, adapter, allow_domains=allow_domains,
            require_approval=require_approval, scrub_pii=scrub_pii,
            allow_private=allow_private, artifacts_dir=artifacts_dir,
        )
        self._sessions[session_id] = agent
        self._browser_sessions[session_id] = browser_session
        self._artifacts[session_id] = {
            "dir": artifacts_dir, "trace": trace, "har": har, "video": video,
        }
        return agent

    def get(self, session_id: str) -> BrowserAgentSession:
        self._prune()
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionRefused("not_found", f"no such session: {session_id}")
        return session

    async def run_test(
        self, *, url: str, steps=None, allow_domains: Optional[list[str]] = None,
        local: bool = False, approve: bool = False, stop_on_failure: bool = False,
        privacy_level: Optional[str] = None, scrub_pii: bool = True,
        network: Optional[dict] = None, resolve_sources: bool = False,
        freeze_animations: bool = True, storage_state: Any = None,
        context_options: Optional[dict] = None, trace: bool = False,
        har: bool = False, video: bool = False,
        artifacts_dir: Optional[str] = None, settle_ms: int = 0,
        detect_dev_server: bool = False, forbid_evaluate: bool = False,
        clock: Optional[dict] = None, throttle: Optional[dict] = None,
        coverage: bool = False,
    ) -> dict:
        """One-shot: open an ephemeral session, run a flow, close, return report.

        The single-call entry point for agents testing a local app. The
        allowlist defaults to the target URL's host; ``local=True`` additionally
        permits loopback/private hosts (the dev server case). ``trace``/``har``/
        ``video`` capture debugging artifacts whose paths are returned.
        """
        host = _host(url).lower()
        if not host:
            raise SessionRefused("invalid_url", f"could not parse a host from {url!r}")
        domains = [d.strip().lower() for d in (allow_domains or []) if d and d.strip()]
        if local or not domains:
            if host not in domains:
                domains.append(host)
        session = await self.open(
            allow_domains=domains, require_approval=False, scrub_pii=scrub_pii,
            privacy_level=privacy_level, allow_private=local,
            storage_state=storage_state, context_options=context_options,
            trace=trace, har=har, video=video, artifacts_dir=artifacts_dir,
        )
        closed: dict = {}
        try:
            flow = normalize_flow_steps(steps) if steps else []
            if not any((s.get("action") or "").lower() == "navigate" for s in flow):
                flow = [{"action": "navigate", "url": url}] + flow
            report = await session.run_flow(
                flow, approve=approve, stop_on_failure=stop_on_failure,
                network=network, resolve_sources=resolve_sources,
                freeze_animations=freeze_animations, settle_ms=settle_ms,
                forbid_evaluate=forbid_evaluate,
                clock=clock, throttle=throttle, coverage=coverage,
            )
            receipts = session.receipts()
        finally:
            closed = await self.close(session.id)
        report["session_id"] = session.id
        report["allow_domains"] = domains
        report["receipts"] = {
            "count": receipts["count"], "merkle_root": receipts["merkle_root"],
        }
        if closed.get("artifacts"):
            report["artifacts"] = closed["artifacts"]
        if detect_dev_server:
            from backend.modules.dev_server import is_loopback, probe_url

            if is_loopback(url):
                try:
                    report["dev_server"] = await probe_url(url)
                except Exception:
                    report["dev_server"] = {"url": url, "reachable": False}
        return report

    def list(self) -> list[dict]:
        self._prune()
        return [s.info() for s in self._sessions.values()]

    async def run_matrix(
        self, *, url: str, steps=None, matrix: Optional[list[dict]] = None,
        local: bool = False, approve: bool = False, network: Optional[dict] = None,
        stop_on_failure: bool = False, resolve_sources: bool = False,
        trace: bool = False, har: bool = False, video: bool = False,
    ) -> dict:
        """Run the same flow across viewports/locales concurrently.

        Each matrix entry may set ``name`` plus any Playwright context option
        (``viewport``, ``locale``, ``user_agent``, ``device_scale_factor``,
        ``timezone_id``). Concurrency is capped at the session limit.
        """
        variants = matrix or [
            {"name": "desktop", "viewport": {"width": 1280, "height": 800}},
            {"name": "mobile", "viewport": {"width": 390, "height": 844}},
        ]
        semaphore = asyncio.Semaphore(max(1, self.max_sessions))
        option_keys = ("viewport", "locale", "user_agent", "device_scale_factor",
                       "timezone_id", "color_scheme", "is_mobile", "has_touch")

        async def one(index: int, variant: dict) -> dict:
            name = variant.get("name") or f"variant-{index}"
            context_options = {
                k: variant[k] for k in option_keys if variant.get(k) is not None
            }
            async with semaphore:
                try:
                    report = await self.run_test(
                        url=url, steps=steps, local=local, approve=approve,
                        stop_on_failure=stop_on_failure, network=network,
                        resolve_sources=resolve_sources, context_options=context_options,
                        trace=trace, har=har, video=video,
                    )
                except Exception as exc:
                    return {"variant": name, "ok": False, "error": str(exc)[:300]}
            failed_steps = [
                {"i": s.get("i"), "action": s.get("action"),
                 "reason": s.get("reason"), "error": s.get("error")}
                for s in report.get("steps") or [] if s.get("status") == "failed"
            ]
            return {
                "variant": name,
                "ok": report.get("ok"),
                "passed": report.get("passed"),
                "failed": report.get("failed"),
                "total": report.get("total"),
                "failed_steps": failed_steps[:5],
                "console_errors": (report.get("console_errors") or [])[:5],
                "artifacts": report.get("artifacts") or {},
            }

        results = await asyncio.gather(
            *(one(i, v) for i, v in enumerate(variants, 1)), return_exceptions=False,
        )
        ok = all(r.get("ok") for r in results)
        return {
            "ok": ok,
            "url": url,
            "variants": results,
            "summary": {
                "variants": len(results),
                "passed": sum(1 for r in results if r.get("ok")),
                "failed": sum(1 for r in results if not r.get("ok")),
            },
        }

    async def close(self, session_id: str) -> dict:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise SessionRefused("not_found", f"no such session: {session_id}")
        session.closed = True
        artifacts: dict = {}
        art = self._artifacts.pop(session_id, None)
        browser_session = self._browser_sessions.pop(session_id, None)
        if browser_session is not None:
            if art and art.get("trace"):
                stop_trace = getattr(browser_session, "stop_trace", None)
                path = os.path.join(art["dir"], "trace.zip")
                if stop_trace is not None:
                    try:
                        if await stop_trace(path):
                            artifacts["trace"] = path
                    except Exception:
                        log.warning("failed to write trace for %s", session_id, exc_info=True)
            try:
                # BrowserSession exposes stop(); tolerate a close() alias.
                teardown = getattr(browser_session, "stop", None) or getattr(
                    browser_session, "close", None,
                )
                if teardown is not None:
                    await teardown()
            except Exception:
                log.warning("failed to close browser session %s", session_id, exc_info=True)
            if art:
                if art.get("har"):
                    har_path = os.path.join(art["dir"], "network.har")
                    if os.path.exists(har_path):
                        artifacts["har"] = har_path
                if art.get("video"):
                    videos = sorted(glob.glob(os.path.join(art["dir"], "*.webm")))
                    if videos:
                        artifacts["video"] = videos[-1]
        else:
            # Artifacts requested but the browser session was faked/absent.
            if art:
                for name, fname in (("har", "network.har"),):
                    candidate = os.path.join(art["dir"], fname)
                    if os.path.exists(candidate):
                        artifacts[name] = candidate
        return {
            "session_id": session_id, "closed": True,
            "steps": len(session.steps), "artifacts": artifacts,
        }


_service: Optional[BrowserAgentService] = None


def get_browser_agent_service() -> BrowserAgentService:
    global _service
    if _service is None:
        _service = BrowserAgentService()
    return _service


def reset_browser_agent_service() -> None:
    """Test hook: drop the singleton (sessions are in-memory)."""
    global _service
    _service = None
