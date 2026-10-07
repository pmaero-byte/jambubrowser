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

import os
from typing import Optional

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

async def run_pointer_step(session, action: str, step: dict, approve: bool,
                           observe: bool) -> Optional[tuple]:
    """Pointer gestures: drag / wheel / mouse / click-at-point / dblclick / set_range.

    Everything else in the vocabulary addresses an element. These describe a
    *movement*, which is the only way to drive a 3D viewport ("drag to rotate,
    scroll to zoom, right-drag to pan"), a native slider, or a press-and-hold.
    Without them the interaction contract of the whole component is untestable
    and the only evidence a viewport works is a screenshot a human eyeballs.

    All of them act on coordinates that the caller may give directly or derive
    from a selector, and all of them require ``approve=true`` for the same
    reason selector clicks do: a gesture lands wherever the pointer is, and the
    risk classifier cannot see through it.
    """
    if action not in ("drag", "wheel", "mouse", "click_at", "dblclick", "set_range"):
        return None

    wait_ms = session._step_timeout_ms(step.get("timeout"))
    selector = (step.get("selector") or "").strip()
    target_ref = ""
    if step.get("ref") or step.get("target") or step.get("name"):
        target_ref = await session._target(step)

    if not approve and not _pointer_is_readonly(action, step):
        session._record(action, "blocked",
                        detail="pointer gestures require approve=true")
        raise SessionRefused(
            "approval_required",
            f"'{action}' dispatches raw pointer events at coordinates the risk "
            f"classifier cannot see; re-send with approve=true",
        )

    button = str(step.get("button") or ("right" if action == "drag"
                                        and step.get("pan") else "left")).lower()
    if button not in ("left", "right", "middle"):
        raise SessionRefused("invalid_step", f"button must be left/right/middle, got {button!r}")
    modifiers = [str(m) for m in (step.get("modifiers") or [])]

    if action == "drag":
        # from/to may be absolute points, or an element plus an offset. The
        # right-drag/pan case is a plain drag with button="right".
        start = step.get("from") or step.get("start") or {}
        end = step.get("to") or step.get("end") or {}
        if target_ref and not selector:
            raise SessionRefused(
                "invalid_step",
                "drag by catalog ref needs a 'selector' or explicit from/to points",
            )
        if not selector:
            _require_point(start, "from")
            _require_point(end, "to")
        elif not end:
            raise SessionRefused(
                "invalid_step",
                "drag by selector needs a 'to' point ({'x','y'} or {'dx','dy'})",
            )
        info = await _dispatch_drag(session, selector, start, end,
                                    step, button, modifiers, wait_ms)
    elif action == "wheel":
        x, y = _point_or_selector(session, step, selector)
        info = await session._call_optional(
            "mouse_wheel", x, y,
            float(step.get("dx", 0) or 0), float(step.get("dy", 0) or 0),
            timeout_ms=wait_ms,
        )
    elif action == "mouse":
        event = str(step.get("event") or step.get("button_event") or "").lower()
        if not event:
            raise SessionRefused(
                "invalid_step", "mouse needs 'event': down, move or up",
            )
        if event not in ("down", "up", "move"):
            # Validated here as well as in the adapter: a typo'd gesture must not
            # silently become a click.
            raise SessionRefused(
                "invalid_step",
                f"mouse event must be down/move/up, got {event!r}",
            )
        x = step.get("x")
        y = step.get("y")
        if (x is None or y is None) and not selector and event == "move":
            raise SessionRefused("target_required", "mouse move needs x/y or a selector")
        info = await session._call_optional(
            "mouse_button", event, button=button,
            x=None if x is None else float(x), y=None if y is None else float(y),
            selector=selector or None,
            steps=int(step.get("steps", 10) or 10),
            modifiers=modifiers,
        )
    elif action == "click_at":
        x, y = _point_or_selector(session, step, selector)
        await session._call_optional("mouse_button", "down", button=button,
                                     x=x, y=y)
        await session._call_optional("mouse_button", "up", button=button)
        info = {"x": x, "y": y, "button": button}
    elif action == "dblclick":
        if selector:
            await session._call_optional("dblclick_selector", selector,
                                         timeout_ms=wait_ms)
            info = {"selector": selector}
        elif target_ref:
            await session._call_optional("dblclick", target_ref, timeout_ms=wait_ms)
            info = {"ref": target_ref}
        else:
            x, y = _point_or_selector(session, step, selector)
            await session._call_optional("mouse_button", "down", button=button, x=x, y=y)
            await session._call_optional("mouse_button", "up", button=button)
            info = {"x": x, "y": y}
    elif action == "set_range":
        if not selector:
            raise SessionRefused(
                "target_required", "set_range needs a 'selector' (input[type=range])",
            )
        raw = step.get("value", step.get("text", step.get("to")))
        if raw is None or str(raw).strip() == "":
            raise SessionRefused("invalid_step", "set_range needs a 'value'")
        info = await session._call_optional(
            "set_range_value", selector, raw,
            press=bool(step.get("press", True)),
            steps=int(step.get("steps", 1) or 1),
            timeout_ms=wait_ms,
        )

    if observe:
        await session._read_state()
    session._capture_step(_recorded_pointer_step(action, step, selector, info))
    return _describe_pointer(action, info), dict(info or {})


def _pointer_is_readonly(action: str, step: dict) -> bool:
    """A pure scroll is observable but harmless; everything else mutates."""
    return action == "wheel" and not step.get("approve", False)


def _require_point(point: dict, field: str) -> dict:
    if not isinstance(point, dict) or ("x" not in point and "dx" not in point):
        raise SessionRefused(
            "invalid_step",
            f"drag '{field}' needs {{'x':…, 'y':…}} (or {{'dx':…, 'dy':…}})",
        )
    return point


def _point_or_selector(session, step: dict, selector: str) -> tuple:
    """Resolve a coordinate from the step.

    Coordinates win. A selector alone is *not* enough to invent a pixel from --
    deriving "the centre" here would mean a second adapter round trip whose
    failure mode is opaque, so the caller is told to use a selector-anchored
    gesture (``drag``/``dblclick``) instead of guessing.
    """
    x, y = step.get("x"), step.get("y")
    if x is None or y is None:
        if not selector:
            raise SessionRefused(
                "target_required", "step needs x/y coordinates or a 'selector'",
            )
        raise SessionRefused(
            "target_required",
            "x/y coordinates are required for this gesture; use a selector-based "
            "drag or dblclick when the element should anchor it",
        )
    return float(x), float(y)


async def _dispatch_drag(session, selector: str, start: dict, end: dict,
                         step: dict, button: str, modifiers: list,
                         wait_ms: int) -> dict:
    """Route a drag through the right adapter call.

    Two shapes, deliberately: a drag anchored on an element (the port moves with
    layout, so the test must not hardcode pixels) and a drag between explicit
    coordinates (a pan across an open canvas).
    """
    steps_n = int(step.get("steps", 20) or 20)
    if selector:
        return await session._call_optional(
            "drag_selector", selector, end, steps=steps_n, button=button,
            modifiers=modifiers, timeout_ms=wait_ms,
        )
    return await session._call_optional(
        "mouse_drag",
        _point(session, start, step),
        _point(session, end, step),
        steps=steps_n, button=button, modifiers=modifiers, timeout_ms=wait_ms,
    )


def _point(session, raw: dict, step: dict) -> dict:
    """``{x, y}`` or a relative ``{dx, dy}`` applied to the step's origin."""
    if not isinstance(raw, dict):
        raise SessionRefused("invalid_step", f"drag point must be an object, got {raw!r}")
    if "x" in raw or "left" in raw:
        return {"x": float(raw.get("x", raw.get("left", 0)) or 0),
                "y": float(raw.get("y", raw.get("top", 0)) or 0)}
    base = {"x": float(step.get("x", 0) or 0), "y": float(step.get("y", 0) or 0)}
    return {"x": base["x"] + float(raw.get("dx", 0) or 0),
            "y": base["y"] + float(raw.get("dy", 0) or 0)}


def _recorded_pointer_step(action: str, step: dict, selector: str,
                           info: Optional[dict]) -> dict:
    """Compact, replayable record of a gesture (no raw coordinate spam)."""
    out: dict = {"action": action}
    if selector:
        out["selector"] = selector
    for key in ("button", "steps", "value", "key"):
        if step.get(key) is not None:
            out[key] = step[key]
    if action == "wheel" and info:
        out["dx"], out["dy"] = info.get("dx"), info.get("dy")
    return out


def _describe_pointer(action: str, info: Optional[dict]) -> str:
    info = info or {}
    if action == "drag":
        return (f"dragged {info.get('button', 'left')} "
                f"({info.get('from')} → {info.get('to')}, {info.get('steps')} steps)")
    if action == "wheel":
        return f"wheeled dx={info.get('dx')} dy={info.get('dy')} at ({info.get('x')}, {info.get('y')})"
    if action == "mouse":
        return f"mouse {info.get('event')} ({info.get('button')})"
    if action == "click_at":
        return f"clicked at ({info.get('x')}, {info.get('y')})"
    if action == "dblclick":
        return f"double-clicked {info.get('selector') or info.get('ref') or info}"
    if action == "set_range":
        return (f"set range to {info.get('value')} "
                f"(range {info.get('min')}..{info.get('max')}"
                f"{', dragged' if info.get('dragged') else ''})")
    return f"{action} done"


async def run_interaction(session, action: str, step: dict, approve: bool, observe: bool) -> Optional[tuple]:
    """click / type / press / hover / select / check / uncheck / dblclick."""

    if action in ("click", "type"):
        selector = (step.get("selector") or "").strip()
        wait_ms = session._step_timeout_ms(step.get("timeout"))
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            if action == "click":
                res = await session.act("click", ref, approve=approve, timeout=wait_ms)
                if observe:
                    await session._read_state()
                return f"clicked {ref}", {"url": res["url"]}
            value = step.get("value", step.get("text", ""))
            await session.act("type", ref, text=value, approve=approve, timeout=wait_ms)
            if observe:
                await session._read_state()
            return f"typed {value[:40]!r} into {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        value = step.get("value", step.get("text", ""))
        res = await session.act_selector(action, selector, text=value,
                                         approve=approve, timeout=wait_ms)
        if observe:
            await session._read_state()
        return (
            f"clicked {selector[:60]}" if action == "click"
            else f"typed {value[:40]!r} into {selector[:60]}"
        ), {"url": res["url"]}

    if action == "press":
        key = step.get("key") or step.get("value") or "Enter"
        selector = (step.get("selector") or "").strip()
        wait_ms = session._step_timeout_ms(step.get("timeout"))
        ref = ""
        if step.get("target") or step.get("ref"):
            ref = await session._target(step)
            await session._call_optional("press", ref, key, timeout_ms=wait_ms)
        elif selector:
            await session._call_optional("press_selector", selector, key,
                                         timeout_ms=wait_ms)
        else:
            await session._call_optional("press", "", key, timeout_ms=wait_ms)
        await session._read_state()
        session._capture_step(
            {"action": "press", "key": key,
             **({"ref": ref} if ref else {}),
             **({"selector": selector} if selector else {})}
        )
        return f"pressed {key}", {}

    if action == "hover":
        selector = (step.get("selector") or "").strip()
        wait_ms = session._step_timeout_ms(step.get("timeout"))
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            await session._call_optional("hover", ref, timeout_ms=wait_ms)
            if observe:
                await session._read_state()
            return f"hovered {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        await session._call_optional("hover_selector", selector, timeout_ms=wait_ms)
        if observe:
            await session._read_state()
        return f"hovered {selector[:60]}", {}

    if action == "select":
        selector = (step.get("selector") or "").strip()
        wait_ms = session._step_timeout_ms(step.get("timeout"))
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            await session._call_optional("select_option", ref, step.get("value", ""),
                                         timeout_ms=wait_ms)
            if observe:
                await session._read_state()
            return f"selected in {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        await session._call_optional("select_selector", selector,
                                     step.get("value", ""), timeout_ms=wait_ms)
        if observe:
            await session._read_state()
        return f"selected in {selector[:60]}", {}

    if action in ("check", "uncheck"):
        selector = (step.get("selector") or "").strip()
        wait_ms = session._step_timeout_ms(step.get("timeout"))
        if step.get("ref") or step.get("target") or step.get("name"):
            ref = await session._target(step)
            await session._call_optional("check", ref, action == "check",
                                         timeout_ms=wait_ms)
            if observe:
                await session._read_state()
            return f"{action} {ref}", {}
        if not selector:
            raise SessionRefused(
                "target_required", "step needs a 'ref', 'target' or 'selector'",
            )
        await session._call_optional("check_selector", selector, action == "check",
                                     timeout_ms=wait_ms)
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
            timeout=session._step_timeout_ms(step.get("timeout")),
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
        path = info.get("path") or ""
        if not path:
            # A download that never produced a file is the interesting failure:
            # a 0-byte export looks like a pass to a name-only assertion.
            raise SessionRefused(
                "download_empty",
                f"download {info.get('file')!r} produced no file (a name "
                f"matched but nothing was written)",
            )
        verified = _verify_download(session, info, step)
        if verified.get("error"):
            # The file exists but does not match what the step asked for. Passing
            # it through would let an empty export look like a successful one.
            raise SessionRefused("download_failed", verified["error"])
        session._capture_step({
            "action": "download", "target": target or ref or selector,
            **({"match": match} if match else {}),
        })
        return (
            f"downloaded {info.get('file')} ({verified.get('bytes', 0)} bytes)",
            {"download": {**info, **verified}},
        )

    if action in ("expect_download", "assert_download"):
        # Verifies the download captured by the previous step. Separate from
        # ``download`` so the content assertions apply to a file the page
        # started on its own (an export kicked off by a background job).
        previous = (session._last_download or {})
        path = previous.get("path") or step.get("path") or ""
        if not path:
            raise SessionRefused(
                "no_download",
                "assert_download with no 'path': run a 'download' step first",
            )
        info = dict(previous)
        info["path"] = path
        verified = _verify_download(session, info, step)
        if verified.get("error"):
            raise SessionRefused("download_failed", verified["error"])
        session._capture_step({
            "action": "assert_download", "target": info.get("file", ""),
        })
        return (
            f"verified {info.get('file')} ({verified.get('bytes')} bytes)",
            {"download": {**info, **verified}},
        )

    return None  # action not handled by this family


def _verify_download(session, info: dict, step: dict) -> dict:
    """Check a saved download against the step's expectations.

    A name match is the weakest possible signal -- an export that produced an
    empty or truncated file still matches by name -- so ``min_bytes``,
    ``sha256`` and ``contains`` are what actually prove the export worked. The
    ``path`` is always returned so a later step can parse the file.
    """
    import hashlib

    path = info.get("path") or ""
    out: dict = {}
    if not path or not os.path.exists(path):
        out["error"] = f"no such file: {path!r}"
        return out
    size = os.path.getsize(path)
    out["bytes"] = size
    min_bytes = step.get("min_bytes", step.get("min_size"))
    if min_bytes is not None and size < int(min_bytes):
        out["error"] = (f"{info.get('file')} is {size} bytes, expected at least "
                        f"{int(min_bytes)} (an empty export?)")
        return out
    if min_bytes is not None:
        out["min_bytes_ok"] = True

    full_digest = step.get("sha256")
    if full_digest:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(131072), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual.lower() != str(full_digest).lower():
            out["error"] = (f"{info.get('file')} sha256 {actual[:16]}… != "
                            f"expected {str(full_digest)[:16]}…")
            out["sha256"] = actual
            return out
        out["sha256"] = actual

    needle = step.get("contains")
    if needle:
        want = needle if isinstance(needle, (bytes, bytearray)) else str(needle).encode()
        with open(path, "rb") as handle:
            blob = handle.read()
        if want not in blob:
            preview = blob[:120].decode("utf-8", "replace")
            out["error"] = (f"{info.get('file')} does not contain "
                            f"{str(needle)[:60]!r} (starts: {preview!r})")
            return out
        out["contains"] = str(needle)[:60]

    # Available for a follow-up step that wants to parse the export.
    session._last_download = {**info, **out}
    return out
