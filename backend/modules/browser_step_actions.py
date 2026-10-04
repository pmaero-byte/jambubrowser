"""Step-action families for the browser test-flow runner.

``BrowserAgentSession._run_step`` used to be one 290-line if-chain: preamble,
navigation, interactions, API calls, file I/O, evaluate, waits, screenshots
and assertions all in a single function. That is the shape where a typo in
one branch silently falls through to "unsupported action" for another, and
where you cannot test a branch without exercising the whole chain.

Split by *family*, dispatched in order. Each handler returns
``(detail, evidence)`` for its own actions and ``None`` for everything else,
so the dispatcher stays a short, readable list and adding an action means
touching exactly one family. Order is preserved from the original chain:
dialog → navigation → interactions → api → files → evaluate → waits →
screenshots → assertions.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.modules.browser_agent_errors import SessionRefused

async def run_dialog_and_navigation(session, action: str, step: dict, observe: bool) -> Optional[tuple]:
    """Dialog arming plus navigate/reload/back/forward."""

    if action == "dialog":
        spec = step.get("dialog") or {
            "accept": step.get("accept", True), "text": step.get("text", ""),
        }
        info = await session.arm_dialog(spec)
        return f"next dialog will be {info['armed']}", {}

    if action == "navigate":
        url = step.get("url") or step.get("value") or ""
        if not url:
            raise SessionRefused("invalid_step", "navigate requires 'url'")
        res = await session.navigate(url)
        if observe:
            await session._read_state()
        if session._settle_ms:
            await session._wait_network_quiet(session._settle_ms)
        return f"→ {res['url']}", {}

    if action in ("reload", "back", "forward"):
        await session._call_optional({"reload": "reload", "back": "go_back",
                                   "forward": "go_forward"}[action])
        await session._read_state()
        if session._settle_ms:
            await session._wait_network_quiet(session._settle_ms)
        return action, {}

    return None  # action not handled by this family

async def run_interaction(session, action: str, step: dict, approve: bool, observe: bool) -> Optional[tuple]:
    """click / type / press / hover / select / check / uncheck."""

    if action in ("click", "type"):
        selector = (step.get("selector") or "").strip()
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            if action == "click":
                res = await session.act("click", ref, approve=approve)
                if observe:
                    await session._read_state()
                return f"clicked {ref}", {"url": res["url"]}
            value = step.get("value", step.get("text", ""))
            await session.act("type", ref, text=value, approve=approve)
            if observe:
                await session._read_state()
            return f"typed {value[:40]!r} into {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        value = step.get("value", step.get("text", ""))
        res = await session.act_selector(action, selector, text=value, approve=approve)
        if observe:
            await session._read_state()
        return (
            f"clicked {selector[:60]}" if action == "click"
            else f"typed {value[:40]!r} into {selector[:60]}"
        ), {"url": res["url"]}

    if action == "press":
        key = step.get("key") or step.get("value") or "Enter"
        selector = (step.get("selector") or "").strip()
        ref = ""
        if step.get("target") or step.get("ref"):
            ref = await session._target(step)
            await session._call_optional("press", ref, key)
        elif selector:
            await session._call_optional("press_selector", selector, key)
        else:
            await session._call_optional("press", "", key)
        await session._read_state()
        session._capture_step(
            {"action": "press", "key": key,
             **({"ref": ref} if ref else {}),
             **({"selector": selector} if selector else {})}
        )
        return f"pressed {key}", {}

    if action == "hover":
        selector = (step.get("selector") or "").strip()
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            await session._call_optional("hover", ref)
            if observe:
                await session._read_state()
            return f"hovered {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        await session._call_optional("hover_selector", selector)
        if observe:
            await session._read_state()
        return f"hovered {selector[:60]}", {}

    if action == "select":
        selector = (step.get("selector") or "").strip()
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            await session._call_optional("select_option", ref, step.get("value", ""))
            if observe:
                await session._read_state()
            return f"selected in {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        await session._call_optional("select_selector", selector, step.get("value", ""))
        if observe:
            await session._read_state()
        return f"selected in {selector[:60]}", {}

    if action in ("check", "uncheck"):
        selector = (step.get("selector") or "").strip()
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            await session._call_optional("check", ref, action == "check")
            if observe:
                await session._read_state()
            return f"{action} {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        await session._call_optional("check_selector", selector, action == "check")
        if observe:
            await session._read_state()
        return f"{action} {selector[:60]}", {}

    return None  # action not handled by this family

async def run_api_step(session, action: str, step: dict, approve: bool) -> Optional[tuple]:
    """api / http / request — a fetch through the page context."""

    if action in ("api", "http", "request"):
        method = (step.get("method") or "GET").upper()
        url = step.get("url") or step.get("value") or ""
        if not url:
            raise SessionRefused("invalid_step", "api requires 'url'")
        session._check_navigation(url)
        mutating = method not in ("GET", "HEAD", "OPTIONS")
        if mutating and not approve:
            session._record(action, "blocked",
                         detail=f"{method} {url} requires approve=true")
            raise SessionRefused(
                "approval_required",
                f"{method} requests mutate remote state; re-send with approve=true",
            )
        response = await session._call_optional(
            "http_request", method, url,
            headers=step.get("headers") or {},
            body=step.get("body", step.get("json", step.get("data"))),
            timeout_ms=int(step.get("timeout", 15000)),
        )
        session._last_api = response
        session._record(action, "ok" if response.get("ok") else "failed",
                     url=session.last_url,
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

    return None  # action not handled by this family

async def run_file_step(session, action: str, step: dict, approve: bool,
                      observe: bool) -> Optional[tuple]:
    """upload / attach_file / set_input_files / download."""

    if action in ("upload", "attach_file", "set_input_files"):
        selector = (step.get("selector") or "").strip()
        chooser = step.get("chooser")
        files = step.get("files", step.get("file"))
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
        elif selector:
            ref = ""
        else:
            raise SessionRefused(
                "target_required", "upload needs a 'ref', 'target' or 'selector'",
            )
        res = await session.upload_files(
            ref, files, approve=approve, selector=selector,
            chooser=None if chooser is None else bool(chooser),
        )
        if observe:
            await session._read_state()
        return (
            f"uploaded {len(res['uploaded'])} file(s) via {res['mode']}",
            {"uploaded": res["uploaded"], "upload_mode": res["mode"]},
        )

    if action == "download":
        selector = (step.get("selector") or "").strip()
        match = str(step.get("match", "") or "")
        dtimeout = int(step.get("timeout", 15000))
        target = step.get("target") or step.get("name") or ""
        if step.get("ref") or target:
            ref = await session._target(step)
            info = await session._call_optional(
                "download_via_click", ref, session.download_dir,
                timeout_ms=dtimeout, match=match,
            )
        elif selector:
            ref = ""
            info = await session._call_optional(
                "download_via_selector", selector, session.download_dir,
                timeout_ms=dtimeout, match=match,
            )
        else:
            raise SessionRefused(
                "target_required", "download needs a 'ref', 'target' or 'selector'",
            )
        info = info or {}
        if info.get("mismatch"):
            raise SessionRefused(
                "download_mismatch",
                f"downloaded {info.get('file')!r}, expected {info['mismatch']!r}",
            )
        session._capture_step({
            "action": "download", "target": target or ref or selector,
            **({"match": match} if match else {}),
        })
        return (
            f"downloaded {info.get('file')} ({info.get('bytes', 0)} bytes)",
            {"download": info},
        )

    return None  # action not handled by this family
