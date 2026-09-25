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
    compile_network,
    diff_elements,
    map_url_for,
    match_network,
    NetworkPolicy,
    parse_stack_frames,
    serialize_body,
    SourceMapData,
)
from backend.modules.meshpay import js_dumps, merkle_root

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

# Actions that change page state and therefore trigger an internal re-observe.
_MUTATING_ACTIONS = {
    "click", "type", "press", "select", "hover", "navigate", "reload",
    "back", "forward", "check", "uncheck", "evaluate",
}

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

SNAPSHOT_JS = """
() => {
  const out = {url: location.href, title: document.title, elements: [], text: ""};
  const sel = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"]';
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const ref = `@e${++i}`;
    el.setAttribute('data-jambu-ref', ref);
    const name = (el.innerText || el.value || el.getAttribute('aria-label')
                  || el.getAttribute('placeholder') || '').trim().slice(0, 120);
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
    });
    if (out.elements.length >= %d) break;
  }
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
        }

    def drain(self) -> dict:
        data = self.snapshot()
        self.console.clear()
        self.page_errors.clear()
        self.failed_requests.clear()
        self.bad_responses.clear()
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
            pass

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

    async def goto(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)

    async def snapshot(self) -> dict:
        return await self._page.evaluate(SNAPSHOT_JS)

    async def click(self, ref: str) -> None:
        await self._page.click(f'[data-jambu-ref="{ref}"]', timeout=10000)

    async def type_text(self, ref: str, text: str) -> None:
        await self._page.fill(f'[data-jambu-ref="{ref}"]', text, timeout=10000)

    async def current_url(self) -> str:
        return self._page.url

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
        target = f'[data-jambu-ref="{ref}"]' if ref else "body"
        await self._page.press(target, key, timeout=10000)

    async def hover(self, ref: str) -> None:
        await self._page.hover(f'[data-jambu-ref="{ref}"]', timeout=10000)

    async def select_option(self, ref: str, value: str) -> None:
        await self._page.select_option(f'[data-jambu-ref="{ref}"]', value, timeout=10000)

    async def check(self, ref: str, checked: bool = True) -> None:
        await self._page.set_checked(f'[data-jambu-ref="{ref}"]', checked, timeout=10000)

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


class SessionRefused(Exception):
    """A safety rail refused the action (allowlist, approval, SSRF)."""

    def __init__(self, reason: str, detail: str = "", candidates: Optional[list] = None):
        self.reason = reason
        self.detail = detail
        self.candidates = candidates or []
        super().__init__(detail or reason)


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


def _json_path(payload: Any, path: str) -> Any:
    """Tiny JSON-path reader for API assertions: ``a.b[0].c`` style.

    Deliberately minimal (no wildcards/filters): QA assertions should be
    readable, and anything fancier belongs in a real schema check.
    """
    if not path:
        return payload
    node = payload
    token = ""
    i = 0
    parts: list[Any] = []
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if token:
                parts.append(token)
                token = ""
        elif ch == "[":
            if token:
                parts.append(token)
                token = ""
            end = path.find("]", i)
            if end < 0:
                return None
            index = path[i + 1:end].strip().strip("'\"")
            parts.append(int(index) if index.isdigit() else index)
            i = end
        else:
            token += ch
        i += 1
    if token:
        parts.append(token)
    for part in parts:
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
            node = node[part]
        else:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
    return node


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


def render_flow_report(report: dict, *, max_errors: int = 5) -> str:
    """Render a flow report as a compact, token-lean Markdown digest."""
    icon = "PASS" if report.get("ok") else "FAIL"
    head = (
        f"# Browser test {icon} — {report.get('passed', 0)}/{report.get('total', 0)} steps "
        f"in {report.get('duration_ms', 0)}ms\n"
        f"final: {report.get('title', '') or '(untitled)'} — {report.get('final_url', '')}\n"
        f"~{report.get('tokens_estimate', estimate_tokens(report))} tokens"
        f"{' · uses JS evaluate' if report.get('uses_evaluate') else ''}"
    )
    lines = [head, ""]
    for step in report.get("steps") or []:
        mark = "ok " if step.get("status") == "passed" else "FAIL"
        bit = f"{mark} #{step.get('i')} {step.get('action')}"
        if step.get("detail"):
            bit += f" — {step['detail']}"
        if step.get("status") == "failed":
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
                "risk": classify_risk(
                    element.get("name") or "", element.get("href") or "",
                ),
            })
        self.catalog = {e["ref"]: e for e in elements if e.get("ref")}
        text = self._scrub((raw.get("text") or "")[:MAX_TEXT_CHARS])
        self.last_url = raw.get("url") or self.last_url
        return {
            "url": self.last_url,
            "title": self._scrub(raw.get("title") or ""),
            "text": text,
            "elements": elements,
            "count": len(elements),
        }

    async def snapshot(self) -> dict:
        state = await self._read_state()
        self._record("snapshot", "ok", url=self.last_url,
                     detail=f"{state['count']} elements")
        return state

    async def act(self, action: str, ref: str, *, text: str = "",
                  approve: bool = False, selector: str = "") -> dict:
        if action not in ("click", "type"):
            raise SessionRefused("unknown_action", f"unsupported action: {action}")
        self._require_agent_control(action)
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
                pass
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
                result["status"] = "failed"
                result["reason"] = refusal.reason
                result["error"] = refusal.detail or refusal.reason
                if refusal.candidates:
                    result["candidates"] = refusal.candidates
                failed += 1
            except Exception as exc:  # unexpected page/tool error
                result["status"] = "failed"
                result["reason"] = "error"
                result["error"] = str(exc)[:300]
                failed += 1

            # Cause attribution: what did this step change / emit?
            cause = self._attribute(before_elements, before_tel)
            if cause:
                result["cause"] = cause
            result["ms"] = int((time.time() - t0) * 1000)
            results.append(result)
            if result["status"] == "failed" and stop_on_failure:
                break

        telemetry = self._drain_telemetry()
        state = await self._safe_state()
        report = {
            "ok": failed == 0,
            "passed": passed,
            "failed": failed,
            "total": len(results),
            "steps": results,
            "console_errors": telemetry.get("console_errors", []),
            "console_warnings": telemetry.get("console_warnings", []),
            "failed_requests": telemetry.get("failed_requests", []),
            "bad_responses": telemetry.get("bad_responses", []),
            "final_url": state.get("url", self.last_url),
            "title": state.get("title", ""),
            "duration_ms": int((time.time() - started) * 1000),
        }
        if network:
            report["network"] = network_info
        report["network_policy"] = (
            self.network_policy.report() if self.network_policy is not None
            else {"enforced": False, "reason": "adapter_without_request_policy"}
        )
        if resolve_sources:
            report["console_errors_source"] = await self._resolve_sources(
                telemetry.get("console_errors_detail") or [],
            )
        report["uses_evaluate"] = any(
            (s.get("action") or "").lower() == "evaluate" for s in normalized
        )
        report["tokens_estimate"] = estimate_tokens(report)
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
        }

    def _attribute(self, before_elements: list[dict], before_tel: dict) -> dict:
        """Summarise the telemetry + DOM changes a step caused."""
        cause: dict = {}
        tel = self._peek_telemetry()
        new_errors = (tel.get("console_errors") or [])[before_tel["console_errors"]:]
        new_failed = (tel.get("failed_requests") or [])[before_tel["failed_requests"]:]
        new_bad = (tel.get("bad_responses") or [])[before_tel["bad_responses"]:]
        if new_errors:
            cause["console_errors"] = [e[:160] for e in new_errors[:3]]
        if new_failed:
            cause["failed_requests"] = new_failed[:3]
        if new_bad:
            cause["bad_responses"] = new_bad[:3]
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

        if action == "navigate":
            url = step.get("url") or step.get("value") or ""
            if not url:
                raise SessionRefused("invalid_step", "navigate requires 'url'")
            res = await self.navigate(url)
            if observe:
                await self._read_state()
            if self._settle_ms:
                await self._wait_network_quiet(self._settle_ms)
            return f"→ {res['url']}", {}

        if action in ("reload", "back", "forward"):
            await self._call_optional({"reload": "reload", "back": "go_back",
                                       "forward": "go_forward"}[action])
            await self._read_state()
            if self._settle_ms:
                await self._wait_network_quiet(self._settle_ms)
            return action, {}

        if action in ("click", "type"):
            selector = (step.get("selector") or "").strip()
            if step.get("ref") or step.get("target") or step.get("name"):
                ref = await self._target(step)
                if action == "click":
                    res = await self.act("click", ref, approve=approve)
                    if observe:
                        await self._read_state()
                    return f"clicked {ref}", {"url": res["url"]}
                value = step.get("value", step.get("text", ""))
                await self.act("type", ref, text=value, approve=approve)
                if observe:
                    await self._read_state()
                return f"typed {value[:40]!r} into {ref}", {}
            if not selector:
                raise SessionRefused(
                    "target_required", "step needs a 'ref', 'target' or 'selector'",
                )
            value = step.get("value", step.get("text", ""))
            res = await self.act_selector(action, selector, text=value, approve=approve)
            if observe:
                await self._read_state()
            return (
                f"clicked {selector[:60]}" if action == "click"
                else f"typed {value[:40]!r} into {selector[:60]}"
            ), {"url": res["url"]}

        if action == "press":
            key = step.get("key") or step.get("value") or "Enter"
            selector = (step.get("selector") or "").strip()
            ref = ""
            if step.get("target") or step.get("ref"):
                ref = await self._target(step)
                await self._call_optional("press", ref, key)
            elif selector:
                await self._call_optional("press_selector", selector, key)
            else:
                await self._call_optional("press", "", key)
            await self._read_state()
            self._capture_step(
                {"action": "press", "key": key,
                 **({"ref": ref} if ref else {}),
                 **({"selector": selector} if selector else {})}
            )
            return f"pressed {key}", {}

        if action in ("api", "http", "request"):
            method = (step.get("method") or "GET").upper()
            url = step.get("url") or step.get("value") or ""
            if not url:
                raise SessionRefused("invalid_step", "api requires 'url'")
            self._check_navigation(url)
            mutating = method not in ("GET", "HEAD", "OPTIONS")
            if mutating and not approve:
                self._record(action, "blocked",
                             detail=f"{method} {url} requires approve=true")
                raise SessionRefused(
                    "approval_required",
                    f"{method} requests mutate remote state; re-send with approve=true",
                )
            response = await self._call_optional(
                "http_request", method, url,
                headers=step.get("headers") or {},
                body=step.get("body", step.get("json", step.get("data"))),
                timeout_ms=int(step.get("timeout", 15000)),
            )
            self._last_api = response
            self._record(action, "ok" if response.get("ok") else "failed",
                         url=self.last_url,
                         detail=f"{method} {url} → {response.get('status')}")
            expect = step.get("expect_status")
            if expect is not None:
                actual = response.get("status", 0)
                wanted = int(expect) if str(expect).isdigit() else None
                if wanted is None:
                    expect_s = str(expect).lower()
                    ok = (expect_s == "2xx" and 200 <= actual < 300) or \
                         (expect_s == "3xx" and 300 <= actual < 400) or \
                         (expect_s == "4xx" and 400 <= actual < 500) or \
                         (expect_s == "5xx" and 500 <= actual < 600)
                else:
                    ok = actual == wanted
                if not ok:
                    raise SessionRefused(
                        "assertion_failed",
                        f"{method} {url} returned {actual}, expected {expect}",
                    )
            detail = (f"{method} {url} → {response.get('status')} "
                      f"in {response.get('latency_ms')}ms")
            return detail, {"api": {
                "status": response.get("status"),
                "latency_ms": response.get("latency_ms"),
                "url": url, "method": method,
                "ok": bool(response.get("ok")),
            }}

        if action == "hover":
            selector = (step.get("selector") or "").strip()
            if step.get("ref") or step.get("target") or step.get("name"):
                ref = await self._target(step)
                await self._call_optional("hover", ref)
                if observe:
                    await self._read_state()
                return f"hovered {ref}", {}
            if not selector:
                raise SessionRefused(
                    "target_required", "step needs a 'ref', 'target' or 'selector'",
                )
            await self._call_optional("hover_selector", selector)
            if observe:
                await self._read_state()
            return f"hovered {selector[:60]}", {}

        if action == "select":
            selector = (step.get("selector") or "").strip()
            if step.get("ref") or step.get("target") or step.get("name"):
                ref = await self._target(step)
                await self._call_optional("select_option", ref, step.get("value", ""))
                if observe:
                    await self._read_state()
                return f"selected in {ref}", {}
            if not selector:
                raise SessionRefused(
                    "target_required", "step needs a 'ref', 'target' or 'selector'",
                )
            await self._call_optional("select_selector", selector, step.get("value", ""))
            if observe:
                await self._read_state()
            return f"selected in {selector[:60]}", {}

        if action in ("check", "uncheck"):
            selector = (step.get("selector") or "").strip()
            if step.get("ref") or step.get("target") or step.get("name"):
                ref = await self._target(step)
                await self._call_optional("check", ref, action == "check")
                if observe:
                    await self._read_state()
                return f"{action} {ref}", {}
            if not selector:
                raise SessionRefused(
                    "target_required", "step needs a 'ref', 'target' or 'selector'",
                )
            await self._call_optional("check_selector", selector, action == "check")
            if observe:
                await self._read_state()
            return f"{action} {selector[:60]}", {}

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
            await self._run_wait(step, timeout)
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
            passed, message = await self._evaluate_assert(step, state)
            if not passed:
                raise SessionRefused("assertion_failed", message)
            return message, {}

        raise SessionRefused("unknown_action", f"unsupported action: {action}")

    async def _run_wait(self, step: dict, timeout: int) -> None:
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

    async def _evaluate_assert(self, step: dict, state: dict) -> tuple[bool, str]:
        action = (step.get("action") or "").strip().lower()
        kind = (step.get("kind") or
                (action[len("assert_"):] if action.startswith("assert_") else "")).strip().lower()
        kind = kind or "visible"
        target = step.get("target") or step.get("name") or ""
        value = str(step.get("value", step.get("expected", "")))

        element = None
        if target:
            try:
                element = self.catalog.get(self.resolve_target(target))
            except SessionRefused:
                element = None

        selector = (step.get("selector") or "").strip()
        if selector and kind in (
            "visible", "not_visible", "hidden", "text", "text_contains",
            "text_equals", "value", "count", "checked", "unchecked",
            "not_checked", "enabled", "disabled",
        ):
            return await self._assert_selector(kind, selector, value)

        if kind in ("no_a11y_violations", "a11y_clean", "accessible"):
            audit = await self._call_optional("a11y_audit")
            issues = (audit or {}).get("issues") or []
            if not issues:
                return True, "no accessibility violations"
            summary = ", ".join(
                f"{i.get('id')}({i.get('count')})" for i in issues[:5]
            )
            return False, f"{len(issues)} a11y issue(s): {summary}"

        if kind in ("perf", "lcp", "fcp", "load", "dom_nodes", "transfer_kb",
                    "resource_count", "navigation_ms"):
            metric = kind if kind != "perf" else (step.get("metric") or "lcp").lower()
            metrics = await self._call_optional("perf_metrics")
            metrics = metrics or {}
            source = {
                "lcp": "lcp_ms", "fcp": "fcp_ms", "load": "load_ms",
                "dom_nodes": "dom_nodes", "resource_count": "resource_count",
                "navigation_ms": "response_ms",
            }.get(metric, metric)
            actual = float(metrics.get(source, 0) or 0)
            if metric == "transfer_kb":
                actual = float(metrics.get("transfer_bytes", 0) or 0) / 1024.0
            budget = float(value or 0)
            ok = actual <= budget if budget > 0 else actual > 0
            return ok, (f"{metric}={actual:.1f} (budget {budget:g})" if budget > 0
                        else f"{metric}={actual:.1f}")

        if kind in ("status", "api_status"):
            if self._last_api is None:
                return False, "no api step ran before this assertion"
            actual = int(self._last_api.get("status") or 0)
            expect = value or "2xx"
            if str(expect).isdigit():
                ok = actual == int(expect)
            else:
                e = str(expect).lower()
                ok = (e == "2xx" and 200 <= actual < 300) or \
                     (e == "3xx" and 300 <= actual < 400) or \
                     (e == "4xx" and 400 <= actual < 500) or \
                     (e == "5xx" and 500 <= actual < 600)
            return ok, f"api status {actual} (expected {expect})"

        if kind in ("latency", "api_latency"):
            if self._last_api is None:
                return False, "no api step ran before this assertion"
            actual = int(self._last_api.get("latency_ms") or 0)
            budget = int(float(value or 0))
            ok = actual <= budget if budget > 0 else actual > 0
            return ok, (f"api latency {actual}ms (budget {budget}ms)"
                        if budget > 0 else f"api latency {actual}ms")

        if kind in ("json", "api_json", "json_path"):
            if self._last_api is None:
                return False, "no api step ran before this assertion"
            payload = self._last_api.get("json")
            if payload is None:
                return False, "api response was not JSON"
            path = (step.get("path") or step.get("json_path") or "")
            expected = (step.get("expected") if step.get("expected") is not None
                        else step.get("value"))
            got = _json_path(payload, path) if path else payload
            if expected is None or expected == "":
                ok = got is not None
                return ok, (f"json {path or '<root>'} present" if ok
                            else f"json {path or '<root>'} missing")
            ok = str(got) == str(expected)
            return ok, (f"json {path or '<root>'}: {got!r}"
                        + ("" if ok else f" != {expected!r}"))

        if kind in ("schema", "api_schema"):
            if self._last_api is None:
                return False, "no api step ran before this assertion"
            payload = self._last_api.get("json")
            required = step.get("required") or step.get("value") or []
            if isinstance(required, str):
                required = [k.strip() for k in required.split(",") if k.strip()]
            if not isinstance(payload, dict):
                return False, "api response was not a JSON object"
            missing = [k for k in required if k not in payload]
            return (not missing), (
                f"schema ok ({len(required)} keys)" if not missing
                else f"missing keys: {', '.join(missing)}")

        if kind in ("header", "api_header"):
            if self._last_api is None:
                return False, "no api step ran before this assertion"
            headers = {k.lower(): str(v) for k, v in
                       (self._last_api.get("headers") or {}).items()}
            key = (step.get("header") or step.get("name") or "").lower()
            got = headers.get(key)
            expected = str(step.get("value", step.get("expected", "")))
            if not expected:
                ok = got is not None
                return ok, (f"header {key} present" if ok
                            else f"header {key} missing")
            ok = got is not None and expected.lower() in got.lower()
            return ok, (f"header {key}: {got!r}")

        if kind == "made_request":
            made = await self._call_optional("made_request", value)
            return bool(made), (f"request made: {value}" if made
                                else f"no matching request: {value}")
        if kind in ("no_request", "request_absent"):
            made = await self._call_optional("made_request", value)
            return (not made), (f"request absent: {value}" if not made
                                else f"unexpected request: {value}")

        if kind == "visible":
            if element is not None:
                ok = element.get("visible") is not False
                return ok, (f"visible: {target}" if ok else f"not visible: {target}")
            # Non-interactive content (headings, panels, text) is not in the
            # element catalog; fall back to rendered page text (innerText
            # respects display:none, so hidden content stays hidden).
            text_hit = (target or "").lower() in (state.get("text") or "").lower()
            return text_hit, (f"visible text: {target}" if text_hit
                              else f"element/text missing: {target}")
        if kind in ("not_visible", "hidden"):
            if element is not None:
                ok = element.get("visible") is False
                return ok, (f"not visible: {target}" if ok else f"still visible: {target}")
            text_hit = (target or "").lower() in (state.get("text") or "").lower()
            return (not text_hit), (f"not visible: {target}" if not text_hit
                                    else f"text still present: {target}")
        if kind in ("text", "text_contains"):
            blob = (element or {}).get("name") if element else state.get("text", "")
            blob = blob or (state.get("text", "") if not element else "")
            ok = value.lower() in (blob or "").lower()
            return ok, (f"text contains {value!r}" if ok else f"text missing {value!r}")
        if kind == "text_equals":
            blob = (element or {}).get("name") if element else state.get("text", "")
            ok = (blob or "").strip() == value.strip()
            return ok, (f"text == {value!r}" if ok else f"text != {value!r}")
        if kind == "value":
            ok = value.lower() in ((element or {}).get("value") or "").lower()
            return ok, (f"value contains {value!r}" if ok else f"value missing {value!r}")
        if kind == "url":
            ok = value in state.get("url", "")
            return ok, (f"url contains {value!r}" if ok else f"url does not contain {value!r}")
        if kind == "title":
            ok = value.lower() in (state.get("title", "") or "").lower()
            return ok, (f"title contains {value!r}" if ok else f"title missing {value!r}")
        if kind == "count":
            if target:
                n = sum(1 for e in self.catalog.values()
                        if target.lower() in (e.get("name") or "").lower())
            else:
                n = len(self.catalog)
            ok = n == int(value or 0)
            return ok, (f"count == {n}" if ok else f"count {n} != {value}")
        if kind == "checked":
            ok = bool(element) and element.get("checked") is True
            return ok, ("checked" if ok else f"not checked: {target}")
        if kind in ("unchecked", "not_checked"):
            ok = element is None or element.get("checked") is not True
            return ok, ("unchecked" if ok else f"checked: {target}")
        if kind == "enabled":
            ok = bool(element) and not element.get("disabled")
            return ok, ("enabled" if ok else f"disabled/missing: {target}")
        if kind == "disabled":
            ok = bool(element) and element.get("disabled")
            return ok, ("disabled" if ok else f"enabled/missing: {target}")
        if kind in ("console_clean", "no_console_errors"):
            errors = self._peek_telemetry().get("console_errors", [])
            return (not errors), ("console clean" if not errors else f"{len(errors)} console error(s)")
        if kind in ("no_failed_requests", "network_clean"):
            failed_reqs = self._peek_telemetry().get("failed_requests", [])
            return (not failed_reqs), ("no failed requests" if not failed_reqs
                                       else f"{len(failed_reqs)} failed request(s)")
        raise SessionRefused("unknown_assertion", f"unsupported assertion kind: {kind}")

    async def _assert_selector(self, kind: str, selector: str, value: str) -> tuple[bool, str]:
        """Assertion kinds answered by direct CSS/XPath probes."""
        label = selector[:60]
        if kind == "visible":
            ok = await self._call_optional("is_visible_selector", selector)
            return bool(ok), (f"visible: {label}" if ok else f"not visible: {label}")
        if kind in ("not_visible", "hidden"):
            ok = await self._call_optional("is_visible_selector", selector)
            return (not ok), (f"not visible: {label}" if not ok else f"still visible: {label}")
        if kind in ("text", "text_contains"):
            blob = await self._call_optional("text_of_selector", selector) or ""
            ok = value.lower() in blob.lower()
            return ok, (f"text contains {value!r}" if ok else f"text missing {value!r}")
        if kind == "text_equals":
            blob = (await self._call_optional("text_of_selector", selector) or "").strip()
            ok = blob == value.strip()
            return ok, (f"text == {value!r}" if ok else f"text != {value!r}")
        if kind == "value":
            current = await self._call_optional("value_of_selector", selector) or ""
            ok = value.lower() in current.lower()
            return ok, (f"value contains {value!r}" if ok else f"value missing {value!r}")
        if kind == "count":
            n = await self._call_optional("count_selector", selector)
            ok = int(n) == int(value or 0)
            return ok, (f"count == {n}" if ok else f"count {n} != {value}")
        if kind == "checked":
            ok = await self._call_optional("is_checked_selector", selector)
            return bool(ok), ("checked" if ok else f"not checked: {label}")
        if kind in ("unchecked", "not_checked"):
            ok = await self._call_optional("is_checked_selector", selector)
            return (not ok), ("unchecked" if not ok else f"checked: {label}")
        if kind == "enabled":
            ok = await self._call_optional("is_enabled_selector", selector)
            return bool(ok), ("enabled" if ok else f"disabled/missing: {label}")
        if kind == "disabled":
            ok = await self._call_optional("is_enabled_selector", selector)
            return (not ok), ("disabled" if not ok else f"enabled/missing: {label}")
        raise SessionRefused("unknown_assertion", f"unsupported assertion kind: {kind}")

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
            allow_private=allow_private,
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
