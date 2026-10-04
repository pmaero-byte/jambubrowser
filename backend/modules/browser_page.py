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
import hashlib
import json
import logging
import os
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
MAX_ELEMENTS = 200
MAX_FRAMES = 12
MAX_TELEMETRY = 100
MAX_TEXT_CHARS = 4000


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


