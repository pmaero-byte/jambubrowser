"""The Playwright page adapter and the observation it produces.

``PlaywrightPage`` is the only place in the agent that talks to a real browser:
navigation, the element catalog, frames, CDP throttling/coverage/a11y/perf and
the telemetry that captures console errors, page errors and failed requests. It
is split out from ``browser_agent.py`` because session logic (approvals,
receipts, flows) and driver logic change for different reasons and are read
from different tracebacks.

Everything here is either an adapter method or a pure helper over the data it
collects, so the interesting parts — coverage arithmetic, catalog limits — can
be tested without a browser.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import re
import tempfile
import time
from typing import Any, Optional, Protocol
from urllib.parse import urljoin

from backend.modules.browser_agent_errors import SessionRefused
from backend.modules.browser_debug import (
    A11Y_JS,
    PERF_JS,
    PERF_OBSERVER_JS,
    NetworkDecision,
    NetworkPolicy,
    compile_network,
    match_network,
    serialize_body,
)

log = logging.getLogger("jambu.browser_page")

# Default timeout for adapter waits (navigation, waits, CDP probes) and for
# a flow step that does not set one. Owned here because the adapter is what
# the timeout applies to; the session imports it rather than redefining it.
DEFAULT_STEP_TIMEOUT_MS = 5000
# Default budget for a click/type/hover on a specific element. Distinct from
# DEFAULT_STEP_TIMEOUT_MS: this used to be a literal 10000 baked into every
# selector dispatch, which is why a step asking for 1500ms still waited 10s.
DEFAULT_ACTION_TIMEOUT_MS = 10000
MAX_ELEMENTS = 200
MAX_FRAMES = 12
MAX_TELEMETRY = 100
MAX_TEXT_CHARS = 4000
# A request body larger than this is recorded as a size, never kept: a file
# upload must not end up in a test report.
MAX_CAPTURED_BODY_BYTES = 64 * 1024


class PageAdapter(Protocol):
    async def goto(self, url: str) -> None: ...
    async def snapshot(self) -> dict: ...
    async def click(self, ref: str) -> None: ...
    async def type_text(self, ref: str, text: str) -> None: ...
    async def current_url(self) -> str: ...


# -- diagnostics JS ---------------------------------------------------------
#
# Injected into the page to answer "could this element have been clicked?".
# Both are read-only: a diagnosis must never change the state it is diagnosing.

DESCRIBE_SELECTOR_JS = r"""
(sel) => {
  // ``sel`` is normally a selector string, but the adapter passes an element
  // handle when the caller used a Playwright engine selector (``text=…``,
  // ``role=…``, ``:has-text(…)``) -- those are not valid CSS, so they cannot be
  // resolved here and must be resolved by Playwright itself first.
  let el = null, viaXPath = false, label = '';
  if (sel && typeof sel === 'object' && typeof sel.tagName === 'string') {
    el = sel;
    label = el.tagName.toLowerCase();
  } else {
    label = String(sel);
    const isX = sel.startsWith('xpath=') || sel.startsWith('//') || sel.startsWith('(//');
    if (isX) {
      viaXPath = true;
      try { el = document.evaluate(sel.startsWith('xpath=') ? sel.slice(6) : sel,
                                  document, null, 9, null).singleNodeValue; }
      catch (e) { return {found: false, error: 'bad xpath: ' + e.message}; }
    } else {
      try { el = document.querySelector(sel); } catch (e) {
        return {found: false, error: 'bad selector: ' + e.message};
      }
    }
  }
  if (!el) return {found: false, selector: label};

  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  const vw = window.innerWidth, vh = window.innerHeight;
  const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;

  const displayed = rect.width > 0 && rect.height > 0
    && style.visibility !== 'hidden' && style.display !== 'none';
  // Off-screen is the single most common cause of "click timed out" on a
  // responsive layout: the element is in the DOM and "visible" per CSS, but
  // its centre sits outside the viewport, so it was never clickable.
  const inViewport = rect.bottom > 0 && rect.right > 0
    && rect.top < vh && rect.left < vw
    && cx >= 0 && cy >= 0 && cx <= vw && cy <= vh;
  const coveredBy = (displayed && inViewport) ? (document.elementFromPoint(cx, cy)) : null;
  const covered = coveredBy && coveredBy !== el && !el.contains(coveredBy)
    && !coveredBy.contains(el) ? coveredBy : null;

  const disabled = el.disabled === true
    || el.getAttribute('aria-disabled') === 'true'
    || (typeof el.matches === 'function' && el.matches(':disabled'));

  // What actually sits on top, described well enough to act on.
  const describe = (node) => {
    if (!node) return null;
    const tag = node.tagName.toLowerCase();
    const id = node.id ? '#' + node.id : '';
    const cls = node.className && typeof node.className === 'string'
      ? '.' + node.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    const label = (node.getAttribute && (node.getAttribute('aria-label')
      || node.getAttribute('data-testid') || node.id || '')) || '';
    return {selector: tag + id + cls, tag: tag, label: label.slice(0, 60),
            text: ((node.innerText || '').trim().slice(0, 40))};
  };

  return {
    found: true,
    selector: label,
    matched_via: viaXPath ? 'xpath' : 'css',
    tag: el.tagName.toLowerCase(),
    text: ((el.innerText || el.textContent || '').trim().slice(0, 80)),
    rect: {x: Math.round(rect.x), y: Math.round(rect.y),
           width: Math.round(rect.width), height: Math.round(rect.height),
           bottom: Math.round(rect.bottom), right: Math.round(rect.right)},
    center: {x: Math.round(cx), y: Math.round(cy)},
    viewport: {width: vw, height: vh},
    displayed: displayed,
    visible: displayed && inViewport,
    in_viewport: inViewport,
    enabled: !disabled,
    hidden_by_css: style.display === 'none' ? 'display:none'
      : (style.visibility === 'hidden' ? 'visibility:hidden'
      : (style.opacity === '0' ? 'opacity:0' : null)),
    off_screen: displayed && !inViewport,
    covered_by: describe(covered),
    // A short verdict beats four booleans at 3am.
    likely_cause: !displayed ? 'not rendered (display/visibility/size)'
      : (disabled ? 'disabled (aria-disabled or [disabled])'
      : (!inViewport ? 'rendered but outside the ' + vw + 'x' + vh + ' viewport'
      : (covered ? 'another element is on top at its centre point'
      : 'element looks clickable; the selector may have resolved elsewhere'))),
  };
}
"""

SUGGEST_SELECTORS_JS = r"""
(sel) => {
  const isX = sel.startsWith('xpath=') || sel.startsWith('//') || sel.startsWith('(//');
  let el = null;
  if (isX) {
    try { el = document.evaluate(sel.startsWith('xpath=') ? sel.slice(6) : sel,
                                document, null, 9, null).singleNodeValue; }
    catch (e) { return []; }
  } else { try { el = document.querySelector(sel); } catch (e) { return []; } }
  if (!el) return [];

  const esc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : String(v).replace(/["\\\]]/g, '\\$&');
  const out = [];
  const push = (v) => { if (v && out.length < 3 && !out.includes(v)) out.push(v); };

  // data-testid first: it is the only spelling that survives a redesign.
  const testid = el.getAttribute('data-testid') || el.getAttribute('data-test');
  if (testid) push('[data-testid="' + esc(testid) + '"]');
  if (el.id) push('#' + esc(el.id));

  const name = (el.getAttribute('aria-label') || el.getAttribute('name')
    || (el.textContent || '').trim().slice(0, 40));
  if (name) {
    const role = el.getAttribute('role')
      || ({BUTTON: 'button', A: 'link', SELECT: 'combobox', INPUT: 'textbox'}[el.tagName]
          || null);
    push(role ? '[aria-label="' + esc(name) + '"]' : '[name="' + esc(name) + '"]');
  }
  const label = el.tagName.toLowerCase();
  if (el.className && typeof el.className === 'string' && el.className.trim()) {
    push(label + '.' + esc(el.className.trim().split(/\s+/)[0]));
  }
  return out;
}
"""


def _is_signature_error(exc: TypeError, fn, name: str) -> bool:
    """True when *exc* is "this callable does not accept these arguments".

    Distinguishes that from a TypeError raised *inside* the adapter (a bug we
    must not swallow or retry).
    """
    message = str(exc)
    markers = (
        "unexpected keyword argument", "takes no arguments", "positional argument",
        "required positional", "got multiple values", "argument after",
    )
    if not any(marker in message for marker in markers):
        return False
    return name in message or "argument" in message


def _accepted_keywords(fn) -> set[str]:
    """Keyword names *fn* accepts, or an empty set if it takes ``**kwargs``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return set()
    names: set[str] = set()
    for param in signature.parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return set()  # accepts anything; do not trim
        if param.kind in (inspect.Parameter.KEYWORD_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD):
            names.add(param.name)
    return names


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
                 network_policy: Optional[NetworkPolicy] = None,
                 artifacts_dir: Optional[str] = None):
        self._page = page
        self.telemetry = Telemetry()
        self._capture_request_bodies = False
        self._capture_tasks: set = set()
        # Console messages attributed to the step that caused them. Playwright
        # reports "The script has an unsupported MIME type ('text/html')" with no
        # URL, so a failure cannot be traced to a file without this.
        self._console_owner: Optional[str] = None
        self._step_console: list[dict] = []
        self._worker_errors: list[dict] = []
        self._workers: list[dict] = []
        # Where screenshot baselines and failing diff frames are written, so they
        # land next to that run's trace/HAR rather than in a random temp dir.
        self.artifacts_dir = artifacts_dir or tempfile.gettempdir()
        self._network = network or {}
        self._network_rules = compile_network(network)
        self.network_policy = network_policy
        self._route_installed = False
        self._route_target = getattr(page, "context", None) or page
        # BrowserContext is needed for CDP (throttling) and coverage; kept
        # explicitly rather than reaching through page.context every call.
        self._context = getattr(page, "context", None)
        self.requests: list[dict] = []
        # Request bodies/status/timings, captured only when a flow asks.
        self.captured_requests: list[dict] = []
        try:
            page.on("console", self._on_console)
            page.on("pageerror", self._on_page_error)
            page.on("worker", self._on_worker)
            page.on("requestfailed", lambda req: self.telemetry.add_failed_request(
                req.method, req.url,
                (req.failure or "") if isinstance(req.failure, str) else str(req.failure or ""),
            ))
            page.on("response", lambda resp: self.telemetry.add_response(
                resp.request.method, resp.url, resp.status,
            ) if resp.status >= 400 else None)
            page.on("request", lambda req: self._track_request(req.method, req.url))
            # Bodies are captured only when asked for: reading every request
            # body on every session is work most flows do not need.
            if self._capture_request_bodies:
                page.on("request", lambda req: self._enqueue_capture(req))
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

    def _on_console(self, msg) -> None:
        location = getattr(msg, "location", None)
        if not isinstance(location, dict):
            location = {}
        self.telemetry.add_console(
            getattr(msg, "type", "log"), getattr(msg, "text", "") or "", location,
        )
        self._attribute_console(msg)

    def _on_page_error(self, exc) -> None:
        text = str(exc)
        self.telemetry.add_page_error(text)
        self._attribute_text("pageerror", text)

    def _attribute_console(self, msg) -> None:
        """Record a console message against the step that is running now.

        The report already groups messages by flow, but "an error happened
        somewhere" is much weaker than "this error happened while step 7 ran".
        """
        location = getattr(msg, "location", None)
        if not isinstance(location, dict):
            location = {}
        self._step_console.append({
            "step": self._console_owner, "level": getattr(msg, "type", "log"),
            "text": (getattr(msg, "text", "") or "")[:300],
            "url": location.get("url", ""), "line": location.get("lineNumber"),
        })
        if len(self._step_console) > MAX_TELEMETRY:
            del self._step_console[: len(self._step_console) - MAX_TELEMETRY]

    def _attribute_text(self, kind: str, text: str) -> None:
        self._step_console.append({
            "step": self._console_owner, "level": "error",
            "text": (text or "")[:300], "url": "", "line": None,
        })

    def begin_step(self, step_label: str) -> None:
        """Mark the start of a step so later console output is attributable."""
        self._console_owner = step_label

    def end_step(self) -> list[dict]:
        """Return and clear the messages attributed to the step just ended."""
        self._console_owner = None
        owned = [c for c in self._step_console if c.get("step") is not None]
        return owned

    def _on_worker(self, worker) -> None:
        """Track workers so their failures surface.

        An in-browser solver runs in a Web Worker. A worker that throws takes
        the computation with it and the page looks fine -- the result simply
        never arrives -- so "did a worker die" is a question the report has to
        answer rather than one the caller has to guess at.
        """
        entry = {"url": str(getattr(worker, "url", "") or "")[:300],
                 "errors": []}
        self._workers.append(entry)
        try:
            worker.on("close", lambda w=worker, e=entry: self._workers.remove(e)
                      if e in self._workers else None)
        except Exception:
            log.debug("worker close event unavailable", exc_info=True)
        try:
            worker.on("pageerror", lambda exc, e=entry: e["errors"].append(str(exc)[:300]))
        except Exception:
            log.debug("worker error event unavailable", exc_info=True)

    def worker_errors(self) -> list[dict]:
        return [{"url": w["url"], "errors": w["errors"]}
                for w in self._workers if w["errors"]]

    def _track_request(self, method: str, url: str) -> None:
        self.requests.append({"method": method, "url": (url or "")[:300]})
        if len(self.requests) > MAX_TELEMETRY:
            del self.requests[: len(self.requests) - MAX_TELEMETRY]

    def _enqueue_capture(self, request) -> None:
        """Schedule the body capture without blocking the event emitter."""
        try:
            task = asyncio.ensure_future(self.capture_request(request))
        except RuntimeError:
            return
        self._capture_tasks.add(task)
        task.add_done_callback(self._capture_tasks.discard)

    def enable_request_capture(self, enabled: bool = True) -> bool:
        """Turn on/off body capture for the rest of the session.

        Installed the first time it is switched on; the Playwright ``request``
        event does not carry a body synchronously, so each captured request
        needs a task awaiting its response.
        """
        self._capture_request_bodies = bool(enabled)
        return self._capture_request_bodies

    def made_request(self, pattern: str) -> bool:
        return any(pattern in r["url"] for r in self.requests)

    async def capture_request(self, request) -> None:
        """Record a request's body, response status and timing (best-effort).

        ``made_request`` answers *whether* a call happened. It cannot answer
        *what was sent*, which is what an agent-bridge or solver-dispatch test
        actually asserts on ("the payload named solver.run and the response
        said ok"). The body is read from Playwright's buffer and dropped unless
        it is small and JSON, so an upload is never retained.

        Never raises: this is diagnostic data collected on a hot path, and a
        failure to read it must not turn a working page into a broken test.
        """
        entry: dict = {
            "method": getattr(request, "method", ""),
            "url": str(getattr(request, "url", ""))[:300],
            "status": None, "body": None, "ms": None,
        }
        try:
            post_data = getattr(request, "post_data", None)
        except Exception:
            post_data = None
        if post_data:
            if len(post_data) <= MAX_CAPTURED_BODY_BYTES:
                try:
                    entry["body"] = json.loads(post_data)
                except (ValueError, TypeError):
                    entry["body_preview"] = post_data[:300]
            else:
                entry["body_omitted"] = f"{len(post_data)} bytes"
        try:
            response = await request.response()
            entry["status"] = response.status
            timing = getattr(request, "timing", None)
            if callable(timing):
                data = timing() or {}
                start, end = data.get("requestStart"), data.get("responseEnd")
                if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                    entry["ms"] = int(end - start)
        except Exception:
            log.debug("could not capture response details", exc_info=True)
        self.captured_requests.append(entry)
        if len(self.captured_requests) > MAX_TELEMETRY:
            del self.captured_requests[: len(self.captured_requests) - MAX_TELEMETRY]

    def captured_matching(self, pattern: str) -> list[dict]:
        """Captured requests whose url, method or body mention *pattern*."""
        out = []
        for entry in self.captured_requests:
            body = entry.get("body")
            haystack = " ".join([
                entry.get("url", ""), entry.get("method", ""),
                json.dumps(body, default=str) if body is not None else "",
                str(entry.get("body_preview", "")),
            ])
            if pattern in haystack:
                out.append(entry)
        return out

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
            async def websocket_handler(ws_route):
                # Playwright hands us the *page-side* route. Two things were
                # wrong here: ``connect_to_server()`` is synchronous (awaiting it
                # raised "object can't be awaited" and took down every snapshot
                # on a page that opened a socket), and once you call it Playwright
                # stops proxying for you -- both directions have to be forwarded
                # by hand or the socket just stalls. A single WebSocket is enough
                # to break every later step, which is how this surfaced.
                decision = await asyncio.to_thread(
                    self.network_policy.decide,
                    ws_route.url, method="GET", kind="websocket",
                )
                if not decision.allowed:
                    await ws_route.close(code=1008, reason=decision.reason)
                    return
                server = ws_route.connect_to_server()
                # server -> page, then page -> server.
                server.on_message(lambda message: ws_route.send(message))
                ws_route.on_message(lambda message: server.send(message))
                ws_route.on_close(
                    lambda code=None, reason=None: server.close(code, reason)
                )
            try:
                await route_websocket("**/*", websocket_handler)
                websocket_supported = True
            except Exception:
                log.debug("websocket routing unavailable", exc_info=True)
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

    async def click(self, ref: str, timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._target_frame(ref).click(self._ref_selector(ref), timeout=timeout_ms)

    async def type_text(self, ref: str, text: str,
                        timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._target_frame(ref).fill(self._ref_selector(ref), text,
                                           timeout=timeout_ms)

    async def dblclick(self, ref: str,
                       timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._target_frame(ref).dblclick(self._ref_selector(ref),
                                               timeout=timeout_ms)

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

    async def wait_for_visible_selector(
        self, selector: str, timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS,
    ) -> None:
        """Wait for an element that is *rendered*, not merely present.

        ``wait_for_selector`` defaults to ``state="attached"``, which matches
        nodes that are laid out but not shown and elements inside a collapsed
        container. A flow waiting for those resumes on a page whose visible
        content is still rendering, so the next step reads a half-built DOM.
        """
        await self._page.wait_for_selector(
            self._engine_selector(selector), state="visible", timeout=timeout_ms,
        )

    async def wait_for_visible_text(
        self, text: str, timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS,
    ) -> None:
        """Wait for text that is actually rendered on screen.

        Playwright's ``get_by_text`` matches hidden text too -- the document
        title, ``<script>`` contents, ``display:none`` copy and offscreen
        nodes. Waiting on ``"workbench"`` therefore returns while the page still
        says "Loading module…", because the title already contained the word.
        """
        await self._page.wait_for_function(
            """([needle]) => {
                const hit = document.createTreeWalker(
                    document.body || document.documentElement,
                    NodeFilter.SHOW_TEXT,
                );
                const want = needle.trim().toLowerCase();
                let node;
                while ((node = hit.nextNode())) {
                    const text = (node.nodeValue || '').trim().toLowerCase();
                    if (!text.includes(want)) continue;
                    const el = node.parentElement;
                    if (!el) continue;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    if (r.width > 0 && r.height > 0
                        && s.visibility !== 'hidden' && s.display !== 'none'
                        && Number(s.opacity || '1') > 0.01
                        && r.bottom > 0 && r.top < window.innerHeight) {
                        return true;
                    }
                }
                return false;
            }""",
            [text], timeout=timeout_ms,
        )

    # -- selector dispatch (CSS/XPath direct addressing) ---------------------

    @staticmethod
    def _engine_selector(selector: str) -> str:
        selector = (selector or "").strip()
        if selector.startswith("xpath="):
            return selector
        if selector.startswith(("//", "(//")):
            return f"xpath={selector}"
        return selector

    async def click_selector(self, selector: str, *,
                             timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._page.click(self._engine_selector(selector), timeout=timeout_ms)

    async def dblclick_selector(self, selector: str, *,
                                timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._page.dblclick(self._engine_selector(selector), timeout=timeout_ms)

    async def fill_selector(self, selector: str, text: str, *,
                            timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._page.fill(self._engine_selector(selector), text, timeout=timeout_ms)

    async def press_selector(self, selector: str, key: str, *,
                             timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._page.press(self._engine_selector(selector), key, timeout=timeout_ms)

    async def hover_selector(self, selector: str, *,
                             timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._page.hover(self._engine_selector(selector), timeout=timeout_ms)

    async def select_selector(self, selector: str, value: str, *,
                              timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._page.select_option(self._engine_selector(selector), value,
                                       timeout=timeout_ms)

    async def check_selector(self, selector: str, checked: bool = True, *,
                             timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        await self._page.set_checked(self._engine_selector(selector), checked,
                                     timeout=timeout_ms)

    # -- pointer gestures -----------------------------------------------------
    #
    # Everything above addresses an element. A 3D viewport, a slider, a map and
    # a native `<input type=range>` are all defined by *movement*, and none of
    # them can be driven by click/hover: "drag to rotate, scroll to zoom,
    # right-drag to pan" is the interaction contract of the whole component.
    # These primitives are what makes that testable, so they mirror Playwright's
    # mouse API rather than inventing a gesture vocabulary.

    @staticmethod
    def _point(source: dict) -> dict:
        """Read an ``{x, y}`` (or ``{from: {x, y}}``) point from a step.

        Accepts CSS lengths as a convenience only for the fixed-viewport case;
        a point is a point.
        """
        x = source.get("x", source.get("left", 0))
        y = source.get("y", source.get("top", 0))
        return {"x": float(x or 0), "y": float(y or 0)}

    async def _element_center(self, selector: str) -> dict:
        """Centre point of the element *selector* resolves to.

        A drag described by an element ("drag across the viewport") has to be
        anchored to where that element actually is, which moves with layout.
        """
        box = await self._page.locator(self._engine_selector(selector)).first.bounding_box()
        if not box:
            raise SessionRefused(
                "target_not_found", f"selector {selector!r} has no bounding box",
            )
        return {"x": box["x"] + box["width"] / 2,
                "y": box["y"] + box["height"] / 2,
                "width": box["width"], "height": box["height"]}

    async def mouse_drag(self, start: dict, end: dict, *,
                         steps: int = 20, button: str = "left",
                         modifiers: Optional[list] = None,
                         timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> dict:
        """Press at *start*, move through *steps* intermediate points, release.

        ``steps`` matters: a single jump is not a drag. Orbit controls, sliders
        with inertia and drag-to-resize all read the movement stream, so a
        one-shot move is often silently ignored.
        """
        mouse = self._page.mouse
        for key in modifiers or []:
            await mouse.down(key=str(key))
        await mouse.move(start["x"], start["y"])
        await mouse.down(button=button)
        try:
            await mouse.move(end["x"], end["y"], steps=max(1, int(steps)))
            await mouse.up(button=button)
        except Exception:
            # Never leave a button held: a stuck left button poisons every
            # later click in the session and looks like a hung page.
            try:
                await mouse.up(button=button)
            except Exception:
                log.debug("mouse.up during drag cleanup failed", exc_info=True)
            raise
        finally:
            for key in modifiers or []:
                try:
                    await mouse.up(key=str(key))
                except Exception:
                    log.debug("modifier release failed", exc_info=True)
        return {"from": start, "to": end, "steps": max(1, int(steps)),
                "button": button}

    async def mouse_wheel(self, x: float, y: float, dx: float = 0, dy: float = 0,
                          *, timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> dict:
        """Scroll/zoom at a viewport position.

        The position is required because zoom-to-cursor reads it: wheeling at
        the middle of the canvas and at the corner are different assertions.
        """
        await self._page.mouse.move(float(x), float(y))
        await self._page.mouse.wheel(float(dx), float(dy))
        return {"x": float(x), "y": float(y), "dx": float(dx), "dy": float(dy)}

    async def mouse_button(self, event: str, *, button: str = "left",
                           x: Optional[float] = None, y: Optional[float] = None,
                           selector: Optional[str] = None,
                           steps: int = 10,
                           modifiers: Optional[list] = None) -> dict:
        """``down`` / ``move`` / ``up`` -- a gesture assembled across steps.

        Needed for press-and-hold and for gestures that must straddle several
        flow steps (press, act, release) such as a right-drag that pauses.
        """
        if event not in ("down", "up", "move"):
            raise SessionRefused(
                "invalid_step", f"mouse event must be down/move/up, got {event!r}",
            )
        if selector:
            point = await self._element_center(selector)
            x, y = point["x"], point["y"]
        elif x is not None and y is not None:
            x, y = float(x), float(y)
        else:
            raise SessionRefused("target_required", "mouse move needs x/y or a selector")

        mouse = self._page.mouse
        if event == "move":
            await mouse.move(x, y, steps=max(1, int(steps)))
        elif event == "down":
            for key in modifiers or []:
                await mouse.down(key=str(key))
            if x is not None:
                await mouse.move(x, y)
            await mouse.down(button=button)
        else:
            await mouse.up(button=button)
            for key in modifiers or []:
                await mouse.up(key=str(key))
        return {"event": event, "button": button, "x": x, "y": y}

    async def drag_selector(self, selector: str, to: dict, *,
                            steps: int = 20, button: str = "left",
                            modifiers: Optional[list] = None,
                            timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> dict:
        """Drag from the centre of *selector* to a point (or an offset)."""
        start = await self._element_center(selector)
        end = dict(self._point(to))
        # An offset ({"dx": 40, "dy": 0}) is relative; an absolute point is not.
        if "dx" in to or "dy" in to:
            end = {"x": start["x"] + float(to.get("dx", 0)),
                   "y": start["y"] + float(to.get("dy", 0))}
        return await self.mouse_drag(start, end, steps=steps, button=button,
                                     modifiers=modifiers, timeout_ms=timeout_ms)

    async def set_range_value(self, selector: str, value: Any, *,
                              press: bool = True, steps: int = 1,
                              timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> dict:
        """Set an ``<input type=range>`` and make it observable.

        Assigning ``.value`` silently does nothing to what the user sees: React
        and most slider widgets track internal state, so the handle does not
        move and the change event does not fire. This sets the value *and*
        dispatches ``input`` + ``change``, then (when ``press``) performs a real
        pointer drag across the track so the app's own gesture handler runs.
        """
        engine_selector = self._engine_selector(selector)
        raw = float(value)
        element = self._page.locator(engine_selector).first
        min_attr = await element.get_attribute("min")
        max_attr = await element.get_attribute("max")
        step_attr = await element.get_attribute("step")
        low = float(min_attr) if min_attr not in (None, "") else 0.0
        high = float(max_attr) if max_attr not in (None, "") else 100.0
        if not (low <= raw <= high):
            raise SessionRefused(
                "invalid_step",
                f"{raw:g} is outside the slider range {low:g}..{high:g}",
            )
        await self._page.evaluate(
            """([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value');
                if (setter && setter.set) setter.set.call(el, String(val));
                else el.value = String(val);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""",
            [engine_selector, raw],
        )
        dragged = False
        if press:
            box = await element.bounding_box()
            if box and box["width"] > 0:
                span = max(0.0, high - low)
                fraction = (raw - low) / span if span else 0.0
                y = box["y"] + box["height"] / 2
                await self.mouse_drag(
                    {"x": box["x"], "y": y},
                    {"x": box["x"] + box["width"] * fraction, "y": y},
                    steps=max(1, int(steps)), timeout_ms=timeout_ms,
                )
                dragged = True
        return {
            "selector": selector, "value": raw, "min": low, "max": high,
            "step": float(step_attr) if step_attr not in (None, "") else None,
            "dragged": dragged,
        }

    # -- diagnostics ----------------------------------------------------------

    async def describe_selector(self, selector: str) -> dict:
        """Everything worth knowing about a selector, for a failure report.

        The question behind most red flows is "was it there and could I have
        hit it?". Answered in one round trip: existence, geometry against the
        viewport, what is painted on top at that point, and whether the element
        would accept input at all.

        Resolution goes through a Playwright locator first so engine selectors
        (``text=Run safe``, ``role=button[name=…]``) work here too -- handing
        them to ``querySelector`` would just report "bad selector" and hide the
        answer for exactly the selectors people write by hand.
        """
        locator = self._page.locator(self._engine_selector(selector)).first
        try:
            count = await locator.count()
        except Exception:
            count = 0
        if not count:
            return {"found": False, "selector": selector}
        handle = await locator.element_handle()
        if handle is None:
            return {"found": False, "selector": selector}
        report = await self._page.evaluate(DESCRIBE_SELECTOR_JS, handle) or {}
        report.setdefault("selector", selector)
        return report

    async def suggest_selectors(self, selector: str) -> list[str]:
        """Up to three alternative spellings for the same element."""
        return await self._page.evaluate(SUGGEST_SELECTORS_JS,
                                         self._engine_selector(selector)) or []

    # -- visual assertions ----------------------------------------------------

    async def screenshot_clip(self, selector: str = "", *,
                              timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> dict:
        """PNG of one element (or the viewport), plus its geometry.

        Visual assertions go through a screenshot rather than reading pixels out
        of the page: a WebGL canvas has no readable backing store after the frame
        is presented (``toDataURL`` returns a cleared, all-zero buffer), so the
        only way to know the viewport rendered *something* is to capture what the
        compositor actually showed.
        """
        if not (selector or "").strip():
            return {"png_base64": await self.screenshot(False)}
        box = await self._element_box(selector)
        clip = await self._clip_to_viewport(box)
        raw = await self._page.screenshot(clip=clip, timeout=timeout_ms)
        # Playwright returns raw PNG bytes; the assertion side decodes base64
        # (matching :meth:`screenshot`), so encode here rather than handing over
        # bytes that will fail to inflate.
        return {"png_base64": base64.b64encode(raw).decode("ascii"), "clip": clip,
                "viewport": await self._viewport_size()}

    async def _element_box(self, selector: str) -> dict:
        """Raw bounding box of the element.

        Playwright rejects a clip that reaches outside the captured surface, and
        an element can be taller than the window or scrolled partly off -- so
        :meth:`_clip_to_viewport` intersects it rather than trusting it.
        """
        box = await self._page.locator(self._engine_selector(selector)).first.bounding_box()
        if not box:
            raise SessionRefused(
                "target_not_found", f"selector {selector!r} has no bounding box",
            )
        return box

    async def _clip_to_viewport(self, box: dict) -> dict:
        view = await self._viewport_size()
        width = max(1.0, min(float(box["width"]),
                              float(view.get("width") or box["width"])))
        height = max(1.0, min(float(box["height"]),
                               float(view.get("height") or box["height"])))
        x = min(max(0.0, float(box["x"])), max(0.0, float(view.get("width") or 0) - width))
        y = min(max(0.0, float(box["y"])), max(0.0, float(view.get("height") or 0) - height))
        return {"x": x, "y": y, "width": width, "height": height}

    async def _viewport_size(self) -> dict:
        size = await self._page.evaluate(
            "() => ({width: window.innerWidth, height: window.innerHeight})",
        )
        return size or {}

    async def diff_screenshot(self, name: str, selector: str = "",
                              threshold: float = 0.005,
                              masks: Optional[list] = None) -> dict:
        """Compare the live frame to a stored baseline; create it if absent.

        Baselines live under ``<artifacts>/baselines/`` so a session with
        artifacts configured keeps them with its traces and HAR, and one
        without still gets a temp dir rather than writing into the repo.
        ``threshold`` is the allowed fraction of differing pixels.
        """


        directory = os.path.join(self.artifacts_dir or tempfile.gettempdir(),
                                 "baselines")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "baseline"
        baseline_path = os.path.join(directory, f"{safe}.png")

        live = await self.screenshot_clip(selector)
        current_b64 = live.get("png_base64") or ""
        if not current_b64:
            return {"error": "no pixels captured", "diff_pct": 100.0}

        if not os.path.exists(baseline_path):
            os.makedirs(directory, exist_ok=True)
            with open(baseline_path, "wb") as handle:
                handle.write(base64.b64decode(current_b64))
            return {"baseline_created": True, "baseline_path": baseline_path,
                    "diff_pct": 0.0, "threshold": threshold}

        with open(baseline_path, "rb") as handle:
            baseline_b64 = base64.b64encode(handle.read()).decode("ascii")

        from backend.modules.browser_assertions import diff_png

        result = diff_png(baseline_b64, current_b64)
        result["baseline_path"] = baseline_path
        result["threshold"] = threshold
        if float(result.get("diff_pct", 0.0)) > threshold:
            # Keep the frame that failed: it is the only evidence of what
            # actually rendered.
            try:
                out_dir = os.path.join(self.artifacts_dir or tempfile.gettempdir(),
                                       "diffs")
                os.makedirs(out_dir, exist_ok=True)
                out_path = os.path.join(out_dir, f"{safe}.png")
                with open(out_path, "wb") as handle:
                    handle.write(base64.b64decode(current_b64))
                result["diff_path"] = out_path
            except Exception:
                log.debug("could not persist the failing diff frame", exc_info=True)
        return result



    async def set_viewport(self, width: int, height: int) -> dict:
        """Resize an already-open context.

        Playwright fixes the viewport at context creation, so the only way to
        change an existing session's geometry is to override it per page. This
        is what makes "screenshot this persistent session at 390x844" possible
        at all instead of spawning a second session.
        """
        await self._page.set_viewport_size({"width": int(width), "height": int(height)})
        return await self._viewport_size()

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


        raw = await self._page.screenshot(full_page=full_page)
        return base64.b64encode(raw).decode("ascii")

    def drain_telemetry(self) -> dict:
        return self.telemetry.drain()

    def peek_telemetry(self) -> dict:
        return self.telemetry.snapshot()




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


