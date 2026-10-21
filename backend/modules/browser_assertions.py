"""Assertion engine for browser test flows.

Extracted from ``browser_agent.py`` so the assertion vocabulary has one
home. The dispatch below is a linear chain because assertion *ordering is
semantics*: selector probes beat page-state probes beat API probes, and a
new kind must not shadow an existing one by accident.

Contract for every handler: return ``(ok, detail)``. ``detail`` is written
into the step result and shown to the caller, so it must explain *what was
compared*, not just pass/fail — a failing assertion nobody can debug is
worse than no assertion.

To add a kind: extend the appropriate helper (``assert_selector`` for CSS /
XPath probes, ``evaluate_assert`` for page/API state) and keep the
"unsupported assertion kind" refusal as the fall-through.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from backend.modules.browser_agent_errors import SessionRefused


def decode_png(png_base64: str) -> tuple[int, int, bytes]:
    """Decode a base64 PNG into ``(width, height, rgb_bytes)``.

    Written out rather than pulled from a dependency because the only thing the
    canvas assertions need is the pixel buffer, and Pillow is not a guaranteed
    install. Uses the stdlib zlib for the IDAT stream.
    """
    import base64
    import struct
    import zlib

    raw = base64.b64decode(png_base64)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, width, height, depth, colour, idat = 8, 0, 0, 0, 0, bytearray()
    while pos + 8 <= len(raw):
        length, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    if depth != 8 or colour not in (2, 6):
        # Palette/greyscale/16-bit frames are not what the assertions produce;
        # refusing is better than silently mis-measuring.
        raise ValueError(f"unsupported PNG format (depth={depth}, colour={colour})")

    channels = 3 if colour == 2 else 4
    data = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(width * height * 3)
    previous = bytearray(stride)
    src = 0
    for row in range(height):
        filter_type = data[src]
        src += 1
        line = bytearray(data[src:src + stride])
        src += stride
        # Undo the per-scanline filters defined in the PNG spec.
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                up_left = previous[i - channels] if i >= channels else 0
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else up_left)
                line[i] = (line[i] + pred) & 0xFF
        for x in range(width):
            base = x * channels
            dst = (row * width + x) * 3
            out[dst] = line[base]
            out[dst + 1] = line[base + 1]
            out[dst + 2] = line[base + 2]
        previous = line
    return width, height, bytes(out)


def analyze_png(png_base64: str, *, quantize: int = 24) -> dict:
    """Measure a frame: size, non-background share, colour count, brightness.

    "Background" is the single most common colour in the frame, which is what a
    blank canvas is; anything else counts as drawn. Colours are quantised into
    ``quantize``-level buckets so a rendered gradient reads as a contour plot
    rather than as "a million colours" (anti-aliasing would otherwise make
    every shaded region look unique).
    """
    width, height, pixels = decode_png(png_base64)
    total = max(1, width * height)
    histogram: dict[int, int] = {}
    brightness_sum = 0
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        key = ((r // quantize) << 16) | ((g // quantize) << 8) | (b // quantize)
        histogram[key] = histogram.get(key, 0) + 1
        brightness_sum += (r * 299 + g * 587 + b * 114) // 1000
    background_count = max(histogram.values()) if histogram else total
    return {
        "width": width, "height": height, "pixels": total,
        "non_background_pct": 100.0 * (total - background_count) / total,
        "colors": len(histogram),
        "brightness": brightness_sum / total,
    }


def diff_png(a_base64: str, b_base64: str, *,
             tolerance: int = 16) -> dict:
    """Compare two frames pixel-wise.

    ``tolerance`` absorbs anti-aliasing and GPU rounding: a channel difference
    at or below it counts as equal, so re-running the same render does not fail
    on dithering noise. Frames of different sizes are reported as fully
    differing rather than raising -- a layout change is exactly the regression a
    baseline should catch.
    """
    aw, ah, a = decode_png(a_base64)
    bw, bh, b = decode_png(b_base64)
    if (aw, ah) != (bw, bh):
        return {"size_mismatch": True, "baseline_size": [aw, ah],
                "current_size": [bw, bh], "diff_pixels": aw * ah,
                "total_pixels": aw * ah, "diff_pct": 100.0}
    total = max(1, aw * ah)
    differing = 0
    for i in range(0, len(a), 3):
        if (abs(a[i] - b[i]) > tolerance or abs(a[i + 1] - b[i + 1]) > tolerance
                or abs(a[i + 2] - b[i + 2]) > tolerance):
            differing += 1
    return {"size_mismatch": False, "width": aw, "height": ah,
            "diff_pixels": differing, "total_pixels": total,
            "diff_pct": 100.0 * differing / total}


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Build a PNG from raw RGB -- the inverse of :func:`decode_png`.

    Used by the tests and by anything that needs a real image to feed the
    canvas/diff assertions, so those paths can be exercised without a browser.
    """
    import struct
    import zlib

    raw = bytearray()
    stride = width * 3
    for row in range(height):
        raw.append(0)  # filter type 0 (None)
        raw += rgb[row * stride:(row + 1) * stride]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def json_path(payload: Any, path: str) -> Any:
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

async def evaluate_assert(session, step: dict, state: dict) -> tuple[bool, str]:
    """Evaluate one ``assert_*`` step against the session's live page state.

    Returns ``(ok, detail)``. Raises :class:`SessionRefused` for a kind the
    vocabulary does not know, so typos surface as refusals instead of a
    silent pass.
    """
    action = (step.get("action") or "").strip().lower()
    kind = (step.get("kind") or
            (action[len("assert_"):] if action.startswith("assert_") else "")).strip().lower()
    kind = kind or "visible"
    target = step.get("target") or step.get("name") or ""
    value = str(step.get("value", step.get("expected", "")))

    element = None
    if target:
        try:
            element = session.catalog.get(session.resolve_target(target))
        except SessionRefused:
            element = None

    selector = (step.get("selector") or "").strip()
    # ``assert_canvas``/``assert_screenshot`` are whole actions, not selector
    # probes, so they are dispatched before the selector narrowing below.
    if kind in ("canvas", "screenshot", "not_screenshot", "diff"):
        return await _assert_visual(session, kind, selector, step)

    if not selector and (step.get("testid") or step.get("test_id")):
        selector = f'[data-testid="{step.get("testid") or step.get("test_id")}"]'
    if not selector and (step.get("role") or (step.get("name") and step.get("role"))):
        selector = _role_selector(step)
    if not selector and step.get("xpath"):
        selector = str(step["xpath"])
    if selector and kind in (
        "visible", "not_visible", "hidden", "text", "text_contains",
        "text_equals", "value", "count", "checked", "unchecked",
        "not_checked", "enabled", "disabled",
    ):
        # nth / within narrow an ambiguous match. "Third Skip button" and "the
        # Save inside the toolbar" were both unreachable before, because a text
        # target threw "ambiguous" with no way to choose. They are also
        # normalised for xpath: an assertion refused xpath outright.
        selector, note = _narrow_selector(selector, step)
        ok, detail = await assert_selector(session, kind, selector, value)
        return ok, f"{note}{detail}" if note else detail

    handled = await assert_content(session, kind, step, value)
    if handled is not None:
        return handled
    handled = await assert_page_health(session, kind, step, value, state)
    if handled is not None:
        return handled
    handled = await assert_api_response(session, kind, step, value)
    if handled is not None:
        return handled
    handled = await assert_network_assertions(session, kind, step, value)
    if handled is not None:
        return handled
    if kind in ("no_request", "request_absent"):
        made = await session._call_optional("made_request", value)
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
            n = sum(1 for e in session.catalog.values()
                    if target.lower() in (e.get("name") or "").lower())
        else:
            n = len(session.catalog)
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
        errors = session._peek_telemetry().get("console_errors", [])
        return (not errors), ("console clean" if not errors else f"{len(errors)} console error(s)")
    if kind == "no_warnings":
        warnings = session._peek_telemetry().get("console_warnings", [])
        return (not warnings), ("no console warnings" if not warnings
                                else f"{len(warnings)} console warning(s)")
    if kind in ("no_failed_requests", "network_clean"):
        failed_reqs = session._peek_telemetry().get("failed_requests", [])
        return (not failed_reqs), ("no failed requests" if not failed_reqs
                                   else f"{len(failed_reqs)} failed request(s)")
    raise SessionRefused("unknown_assertion", f"unsupported assertion kind: {kind}")

async def _assert_visual(session, kind: str, selector: str,
                         step: dict) -> tuple[bool, str]:
    """Route the whole-frame/visual assertion kinds.

    They are actions rather than selector probes because their evidence is the
    rendered frame: the target may be a canvas (which has no readable pixel
    buffer once presented), the whole viewport, or a baseline image on disk.
    """
    if kind == "canvas":
        if not selector:
            raise SessionRefused(
                "invalid_step",
                "assert_canvas needs a 'selector' (the canvas or element to measure)",
            )
        return await assert_canvas(session, selector, step)
    if kind in ("screenshot", "diff"):
        return await assert_screenshot(session, kind, selector, step)
    return await assert_screenshot(session, kind, selector, step)


def _role_selector(step: dict) -> str:
    """A getByRole-style ``role`` + accessible ``name`` target, as a selector.

    ``role``+``name`` is the most durable way to address a control that has no
    test id, so it is supported directly rather than forcing a hand-written CSS
    or xpath. Implemented with Playwright's ``internal:role`` engine selector,
    which is what ``getByRole`` compiles to.
    """
    role = str(step.get("role") or "").strip()
    name = str(step.get("name") or "").strip()
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    base = f'internal:role={role}'
    return f'{base}[name="{escaped}" sss]' if name else base


def _narrow_selector(selector: str, step: dict) -> tuple[str, str]:
    """Apply ``nth`` / ``within`` to a selector, returning it plus a note.

    Both exist because a duplicated label is normal in real UI ("Skip" in a
    header and a "Skip" in a footer) and "ambiguous" with no way to choose is
    not a usable assertion. The note goes into the step detail so a pass/fail
    still says which element it actually looked at.
    """
    note = ""
    within = (step.get("within") or "").strip()
    if within:
        if _is_xpath(selector) or _is_xpath(within):
            selector = f"xpath=({within})//{selector[6:] if _is_xpath(selector) else selector}"
        else:
            selector = f"{within} >> {selector}"
        note = f"within {within[:40]}! "
    nth = step.get("nth")
    if nth is not None and str(nth).strip() != "":
        try:
            index = int(nth)
        except (TypeError, ValueError):
            raise SessionRefused(
                "invalid_step", f"nth must be an integer, got {nth!r}",
            ) from None
        if index < 0:
            index = max(1, index + 1)  # negative counts back from the end
        selector = f"{selector} >> nth={index}"
        note = f"{note}nth={index}: "
    return selector, note


def _is_xpath(selector: str) -> bool:
    selector = (selector or "").strip()
    return selector.startswith(("xpath=", "//", "(//", ".//"))


async def assert_canvas(session, selector: str, step: dict) -> tuple[bool, str]:
    """Assert that a canvas (or any element's box) actually rendered content.

    Reading pixels back out of a WebGL canvas does not work: after the frame is
    presented the drawing buffer is cleared, so ``toDataURL`` and a 2D read of
    the canvas both return zeros. The rendered frame only exists in the
    compositor's output, which a screenshot captures. So this asserts on a
    clipped screenshot:

      ``min_non_background_pct`` -- % of pixels differing from the modal
          background colour. The "did anything draw at all" check: a blank
          viewport is one uniform colour, a rendered one is not.
      ``min_colors`` -- distinct quantised colours, i.e. "this is a shaded
          contour plot" rather than "this is two flat areas".
      ``max_colors`` -- the negative case, for "the empty state must be blank".
      ``min_brightness`` / ``max_brightness`` -- catches an all-black or
          all-white frame that technically has colour variation.

    Evidence (the PNG and the measured numbers) is attached to the step result.
    """
    min_non_background = float(step.get("min_non_background_pct", 0.0) or 0.0)
    min_colors = int(step.get("min_colors", 0) or 0)
    max_colors = step.get("max_colors")
    min_brightness = step.get("min_brightness")
    max_brightness = step.get("max_brightness")
    min_width = step.get("min_width")
    min_height = step.get("min_height")

    if not (min_non_background or min_colors or max_colors
            or min_brightness is not None or max_brightness is not None
            or min_width is not None or min_height is not None):
        raise SessionRefused(
            "invalid_step",
            "assert_canvas needs at least one bound: min_non_background_pct, "
            "min_colors, max_colors, min_brightness, max_brightness, "
            "min_width or min_height",
        )

    shot = await session._call_optional("screenshot_clip", selector)
    png_b64 = (shot or {}).get("png_base64") or ""
    if not png_b64:
        return False, (f"canvas {selector[:40]} captured no pixels — nothing "
                       f"rendered (element may be display:none or zero-sized)")
    stats = analyze_png(png_b64)

    label = selector[:60]
    problems = []
    if min_non_background and stats["non_background_pct"] < min_non_background:
        problems.append(
            f"only {stats['non_background_pct']:.1f}% non-background pixels "
            f"(want >= {min_non_background:.1f}%)"
        )
    if min_colors and stats["colors"] < min_colors:
        problems.append(
            f"{stats['colors']} distinct colours (want >= {min_colors})"
        )
    if max_colors is not None and stats["colors"] > int(max_colors):
        problems.append(
            f"{stats['colors']} distinct colours (want <= {int(max_colors)})"
        )
    if min_brightness is not None and stats["brightness"] < float(min_brightness):
        problems.append(
            f"brightness {stats['brightness']:.1f} (want >= {min_brightness})"
        )
    if max_brightness is not None and stats["brightness"] > float(max_brightness):
        problems.append(
            f"brightness {stats['brightness']:.1f} (want <= {max_brightness})"
        )
    # Geometry bounds, because colour alone cannot tell a rendered viewport
    # from a collapsed one: a responsive bug that squeezes the canvas to a 3px
    # sliver still has colour, so `min_colors` and `min_non_background_pct`
    # both pass on it.
    if min_width is not None and stats["width"] < float(min_width):
        problems.append(
            f"rendered width {stats['width']}px (want >= {int(min_width)}px) -- "
            f"the viewport is collapsed"
        )
    if min_height is not None and stats["height"] < float(min_height):
        problems.append(
            f"rendered height {stats['height']}px (want >= {int(min_height)}px) -- "
            f"the viewport is collapsed"
        )

    measured = (f"{label}: {stats['width']}x{stats['height']}, "
                f"{stats['non_background_pct']:.1f}% non-background, "
                f"{stats['colors']} colours, brightness {stats['brightness']:.1f}")
    if problems:
        return False, f"canvas assertion failed — {measured}; " + "; ".join(problems)
    return True, f"canvas rendered — {measured}"


async def assert_screenshot(session, kind: str, selector: str,
                            step: dict) -> tuple[bool, str]:
    """Compare the current frame against a stored baseline (visual regression).

    ``{"action": "assert_screenshot", "name": "results-contour"}`` diffs the
    live frame against ``<artifacts>/baselines/<name>.png``. The first run of a
    baseline that does not exist *creates* it and passes with
    ``baseline_created`` — that is the one behaviour worth knowing about,
    because a silently created baseline asserts nothing.

    ``threshold`` is the fraction of differing pixels allowed (default 0.5%).
    ``masks`` are ``[selector, …]`` or ``{"selector": …, "color": …}`` regions
    excluded from the diff, for the clock, the fps counter and anything else
    that legitimately moves between runs.
    """
    name = str(step.get("name") or step.get("baseline") or "").strip()
    if not name:
        raise SessionRefused("invalid_step", "assert_screenshot needs a 'name'")
    threshold = float(step.get("threshold", 0.005) or 0.0)
    shots = await session._call_optional(
        "diff_screenshot", name, selector, threshold, step.get("masks") or [],
        baseline_dir=step.get("baseline_dir") or "",
        update=bool(step.get("update_baseline")),
        tolerance=int(step.get("tolerance", 16)))
    if shots.get("baseline_updated"):
        return True, f"baseline {name!r} rewritten (update mode) — nothing asserted"

    if shots.get("baseline_created"):
        return True, (f"baseline {name!r} created — no comparison on the first "
                      f"run; re-run to assert")
    if kind == "not_screenshot":
        differing = float(shots.get("diff_pct", 0.0))
        return differing / 100.0 > threshold, (
            f"differs from baseline {name!r} by {differing:.2f}% "
            f"(want > {threshold * 100:.2f}%)")
    if shots.get("error"):
        return False, f"screenshot baseline {name!r}: {shots['error']}"
    diff_pct = float(shots.get("diff_pct", 0.0))
    if diff_pct / 100.0 <= threshold:
        return True, (f"matches baseline {name!r} ({diff_pct:.2f}% differing, "
                      f"threshold {threshold * 100:.2f}%)")
    return False, (
        f"differs from baseline {name!r} by {diff_pct:.2f}% "
        f"({shots.get('diff_pixels')} pixels, threshold "
        f"{threshold * 100:.2f}%) — capture is saved for inspection"
        + (f" ({shots['diff_path']})" if shots.get("diff_path") else ""))


async def assert_selector(session, kind: str, selector: str, value: str) -> tuple[bool, str]:
    """Assertion kinds answered by direct CSS/XPath probes."""
    label = selector[:60]
    if kind == "visible":
        ok = await session._call_optional("is_visible_selector", selector)
        return bool(ok), (f"visible: {label}" if ok else f"not visible: {label}")
    if kind in ("not_visible", "hidden"):
        ok = await session._call_optional("is_visible_selector", selector)
        return (not ok), (f"not visible: {label}" if not ok else f"still visible: {label}")
    if kind in ("text", "text_contains"):
        blob = await session._call_optional("text_of_selector", selector) or ""
        ok = value.lower() in blob.lower()
        return ok, (f"text contains {value!r}" if ok else f"text missing {value!r}")
    if kind == "text_equals":
        blob = (await session._call_optional("text_of_selector", selector) or "").strip()
        ok = blob == value.strip()
        return ok, (f"text == {value!r}" if ok else f"text != {value!r}")
    if kind == "value":
        current = await session._call_optional("value_of_selector", selector) or ""
        ok = value.lower() in current.lower()
        return ok, (f"value contains {value!r}" if ok else f"value missing {value!r}")
    if kind == "count":
        n = await session._call_optional("count_selector", selector)
        ok = int(n) == int(value or 0)
        return ok, (f"count == {n}" if ok else f"count {n} != {value}")
    if kind == "checked":
        ok = await session._call_optional("is_checked_selector", selector)
        return bool(ok), ("checked" if ok else f"not checked: {label}")
    if kind in ("unchecked", "not_checked"):
        ok = await session._call_optional("is_checked_selector", selector)
        return (not ok), ("unchecked" if not ok else f"checked: {label}")
    if kind == "enabled":
        ok = await session._call_optional("is_enabled_selector", selector)
        return bool(ok), ("enabled" if ok else f"disabled/missing: {label}")
    if kind == "disabled":
        ok = await session._call_optional("is_enabled_selector", selector)
        return (not ok), ("disabled" if not ok else f"enabled/missing: {label}")
    raise SessionRefused("unknown_assertion", f"unsupported assertion kind: {kind}")


async def assert_page_health(session, kind: str, step: dict, value: str, state: dict) -> tuple[bool, str]:
    """Dialog / accessibility / performance assertions (live page health)."""

    if kind in ("dialog", "no_dialog", "no_dialogs"):
        dialogs = session._peek_telemetry().get("dialogs") or []
        if kind in ("no_dialog", "no_dialogs"):
            if not dialogs:
                return True, "no dialog was raised"
            return False, (
                f"{len(dialogs)} dialog(s) raised; last: "
                f"{dialogs[-1].get('type')}:{str(dialogs[-1].get('message', ''))[:60]}"
            )
        if not dialogs:
            return False, "no dialog was raised"
        last = dialogs[-1]
        want_type = str(step.get("type", "") or "")
        if want_type and last.get("type") != want_type:
            return False, f"last dialog was {last.get('type')}, wanted {want_type}"
        if value and value not in str(last.get("message") or ""):
            return False, (
                f"dialog message {str(last.get('message'))[:80]!r} "
                f"does not contain {value!r}"
            )
        if step.get("accepted") is not None:
            if bool(last.get("accepted")) != bool(step.get("accepted")):
                return False, (
                    "dialog was " + ("accepted" if last.get("accepted") else "dismissed")
                )
        state_bit = ""
        if step.get("accepted") is not None:
            state_bit = " accepted" if last.get("accepted") else " dismissed"
        return True, f"dialog {last.get('type')}{state_bit}"

    if kind in ("no_a11y_violations", "a11y_clean", "accessible"):
        audit = await session._call_optional("a11y_audit")
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
        metrics = await session._call_optional("perf_metrics")
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


    return None  # kind not handled by this family
async def assert_network_assertions(session, kind: str, step: dict,
                                    value: str) -> Optional[tuple]:
    """Assertions about what the *page itself* sent over the wire.

    ``assert_api_response`` covers requests the flow issued explicitly through
    the ``api`` action. These cover the calls the application makes on its own,
    which is where a dispatch bug lives: the UI can look perfectly correct while
    having sent the wrong payload. Family returns ``None`` for any other kind.
    """
    if kind in ("request_body", "request_sent", "request_payload"):
        return await assert_request_body(session, step, value)
    if kind in ("request_status", "request_response_status"):
        return await assert_request_status(session, step, value)
    if kind == "request_fast":
        budget = step.get("max_ms", step.get("ms", 500))
        matches = await _captured(session, value)
        if not matches:
            return False, f"no captured request matching {value!r}"
        slowest = max((m.get("ms") or 0) for m in matches)
        ok = slowest <= float(budget)
        return ok, (f"request within {budget}ms budget (slowest {slowest}ms)"
                    if ok else f"slowest matching request took {slowest}ms "
                               f"(budget {budget}ms)")
    return None


async def assert_api_response(session, kind: str, step: dict, value: str) -> tuple[bool, str]:
    """Assertions over the last ``api`` step response (status/latency/json/schema/header)."""

    if kind in ("status", "api_status"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        actual = int(session._last_api.get("status") or 0)
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
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        actual = int(session._last_api.get("latency_ms") or 0)
        budget = int(float(value or 0))
        ok = actual <= budget if budget > 0 else actual > 0
        return ok, (f"api latency {actual}ms (budget {budget}ms)"
                    if budget > 0 else f"api latency {actual}ms")

    if kind in ("json", "api_json", "json_path"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        payload = session._last_api.get("json")
        if payload is None:
            return False, "api response was not JSON"
        path = (step.get("path") or step.get("json_path") or "")
        expected = (step.get("expected") if step.get("expected") is not None
                    else step.get("value"))
        op = step.get("op") or ("regex" if step.get("regex") else "")
        if op:
            # Typed comparison (equals compares JSON values, not strings).
            from backend.modules.content_checks import json_path_check
            try:
                ok, detail = json_path_check(
                    payload, path, op, step.get("regex") if op == "regex" and
                    step.get("regex") else expected)
            except ValueError as exc:
                raise SessionRefused("invalid_step", str(exc)) from exc
            return ok, detail
        got = json_path(payload, path) if path else payload
        if expected is None or expected == "":
            ok = got is not None
            return ok, (f"json {path or '<root>'} present" if ok
                        else f"json {path or '<root>'} missing")
        ok = str(got) == str(expected)
        return ok, (f"json {path or '<root>'}: {got!r}"
                    + ("" if ok else f" != {expected!r}"))

    if kind in ("schema", "api_schema"):
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        payload = session._last_api.get("json")
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
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        headers = {k.lower(): str(v) for k, v in
                   (session._last_api.get("headers") or {}).items()}
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
        made = await session._call_optional("made_request", value)
        return bool(made), (f"request made: {value}" if made
                            else f"no matching request: {value}")

    return None  # kind not handled by this family


async def _captured(session, pattern: str) -> list[dict]:
    """Matching captured requests, enabling capture if it was not on."""
    await session._call_optional("enable_request_capture", True)
    return await session._call_optional("captured_matching", pattern) or []


async def assert_request_body(session, step: dict, value: str) -> tuple[bool, str]:
    """Assert on what a request actually sent, not just that it happened."""
    url = str(step.get("url") or step.get("request") or value or "")
    body_path = str(step.get("body_path") or step.get("path") or "")
    expected = step.get("body_contains", step.get("expected_body"))
    equals = step.get("body_equals", step.get("equals"))
    method = step.get("method")

    matches = await _captured(session, url)
    if method:
        wanted = str(method).upper()
        matches = [m for m in matches if (m.get("method") or "").upper() == wanted]
    if not matches:
        return False, (f"no captured request matching {url!r}"
                       + (f" method {method}" if method else ""))

    last = matches[-1]
    body = last.get("body")
    if body is None:
        preview = last.get("body_preview")
        if preview is not None:
            return False, (f"request body was not JSON (preview: {preview[:120]!r}); "
                           f"cannot assert on it")
        return False, (f"request body not captured (perhaps {last.get('body_omitted')}"
                       f"); enable body capture before the request")

    if body_path:
        found = json_path(body, body_path)
        ok = found is not None
        if ok and equals is not None:
            ok = str(found) == str(equals)
        if ok and isinstance(expected, str) and expected:
            ok = expected in str(found)
        return ok, (f"{body_path} = {found!r}" if ok
                    else f"{body_path} was {found!r}, expected "
                         f"{equals!r}" if equals is not None
                         else f"{body_path} missing from the request body "
                              f"(keys: {sorted(body)[:10] if isinstance(body, dict) else 'n/a'})")

    if isinstance(expected, (dict, list)):
        ok = _contains(body, expected)
        return ok, ("request body contains the expected structure" if ok
                    else f"request body {json.dumps(body, default=str)[:200]} "
                         f"does not contain {json.dumps(expected, default=str)}")
    if isinstance(expected, str) and expected:
        ok = expected in json.dumps(body, default=str)
        return ok, ("request body contains the expected value" if ok
                    else f"request body {json.dumps(body, default=str)[:200]} "
                         f"does not contain {expected!r}")
    return True, f"request matched {url!r} (body captured)"


async def assert_request_status(session, step: dict, value: str) -> tuple[bool, str]:
    """Assert on the status a matching request came back with."""
    url = str(step.get("url") or step.get("request") or value or "")
    matches = await _captured(session, url)
    if not matches:
        return False, f"no captured request matching {url!r}"
    statuses = [m.get("status") for m in matches]
    ok = any(value in str(s) for s in statuses if s is not None)
    return ok, (f"response status {statuses[-1]} matches {value!r}" if ok
                else f"response statuses were {statuses}, wanted {value!r}")


def _contains(haystack, needle) -> bool:
    """Recursive containment for a nested body subset."""
    if isinstance(needle, dict):
        if not isinstance(haystack, dict):
            return False
        return all(key in haystack and _contains(haystack[key], value)
                   for key, value in needle.items())
    if isinstance(needle, list):
        if not isinstance(haystack, list):
            return False
        return all(any(_contains(item, want) for item in haystack)
                   for want in needle)
    return haystack == needle


# --------------------------------------------------------------------------
# Content assertions: JSON schema, SVG structure, text fidelity, prompt
# coverage, determinism and raster glyph fidelity. The checks themselves are
# pure functions in ``content_checks`` / ``glyph_raster``; these wrappers only
# fetch the content to check and format the outcome.

CONTENT_KINDS = ("json_schema", "body", "svg", "text_fidelity", "prompt_coverage",
                 "deterministic", "glyph_fidelity")


def _note(session, data: dict) -> None:
    """Attach structured evidence to the current assertion's step result."""
    try:
        session._assert_data = data
    except AttributeError:  # pragma: no cover - exotic session doubles
        pass


async def content_source(session, step: dict) -> tuple:
    """Return ``(text, origin)`` for a content assertion.

    ``selector`` reads the first match's ``outerHTML`` from the live page;
    otherwise the last ``api`` response is used: ``path`` picks a JSON
    value, else the full response body.
    """
    from backend.modules.content_checks import _MISSING, resolve_path

    selector = (step.get("selector") or "").strip()
    if selector and (step.get("source") or "page") == "page":
        html_text = await session._call_optional(
            "evaluate",
            "sel => { const el = document.querySelector(sel);"
            " return el ? el.outerHTML : null; }",
            selector,
        )
        if html_text is None:
            raise SessionRefused("assertion_failed", f"no element matches {selector!r}")
        return str(html_text), f"page {selector}"
    if session._last_api is None:
        raise SessionRefused("assertion_failed", "no api step ran before this assertion")
    path = step.get("path") or step.get("json_path") or ""
    if path:
        payload = session._last_api.get("json")
        if payload is None:
            raise SessionRefused("assertion_failed", "api response was not JSON")
        got = resolve_path(payload, path)
        if got is _MISSING:
            raise SessionRefused("assertion_failed", f"json {path} missing")
        return (got if isinstance(got, str) else json.dumps(got, ensure_ascii=False)), \
            f"api json {path}"
    body = session._last_api.get("body")
    if body is None:
        body = session._last_api.get("text") or ""
    return str(body), "api body"


async def assert_content(session, kind: str, step: dict, value: str):
    from backend.modules import content_checks as cc

    if kind not in CONTENT_KINDS:
        return None

    if kind == "json_schema":
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        payload = session._last_api.get("json")
        if payload is None:
            return False, "api response was not JSON"
        schema = step.get("schema")
        if not isinstance(schema, dict):
            raise SessionRefused("invalid_step", "assert_json_schema needs a 'schema' object")
        path = step.get("path") or ""
        target = cc.resolve_path(payload, path) if path else payload
        if target is cc._MISSING:
            return False, f"json {path} missing"
        errors = cc.validate_schema(target, schema, "$" + ("." + path if path else ""))
        _note(session, {"schema_errors": errors})
        return (not errors), (f"schema ok ({path or '<root>'})" if not errors else
                              f"{len(errors)} schema error(s): " + "; ".join(errors[:5]))

    if kind == "body":
        text, origin = await content_source(session, step)
        checks = []
        for needle in step.get("contains") or ([] if step.get("regex") else [value] if value
                                               else []):
            checks.append((needle in text, f"contains {needle!r}"))
        for needle in step.get("not_contains") or []:
            checks.append((needle not in text, f"lacks {needle!r}"))
        if step.get("regex"):
            import re
            checks.append((re.search(step["regex"], text) is not None,
                           f"matches /{step['regex']}/"))
        if not checks:
            raise SessionRefused("invalid_step",
                                 "assert_body needs contains, not_contains or regex")
        failed = [label for ok, label in checks if not ok]
        snippet = text[:160].replace("\n", " ")
        return (not failed), (f"{origin}: " + ("; ".join(label for _, label in checks)
                                               if not failed else
                                               "FAILED " + "; ".join(failed)
                                               + f" — body starts {snippet!r}"))

    if kind == "svg":
        text, origin = await content_source(session, step)
        checks = step.get("checks") or {"no_nan": True, "unique_ids": True}
        try:
            results = cc.svg_checks(text, checks)
        except ValueError as exc:
            raise SessionRefused("invalid_step", str(exc)) from exc
        _note(session, {"svg_checks": results})
        failed = [r for r in results if not r["ok"]]
        return (not failed), (f"{origin}: {len(results) - len(failed)}/{len(results)} svg "
                              f"checks passed" + ("" if not failed else "; FAILED "
                                                  + "; ".join(f"{r['check']}: {r['detail']}"
                                                              for r in failed)))

    if kind == "text_fidelity":
        text, origin = await content_source(session, step)
        expected = str(step.get("expected") or value or "")
        if not expected:
            raise SessionRefused("invalid_step", "assert_text_fidelity needs 'expected'")
        if text.lstrip().startswith("<"):
            root, error = cc.parse_svg(text)
            runs = cc.svg_text_runs(root) if root is not None else [text]
        else:
            runs = [text]
        reports = [cc.text_fidelity(expected, run) for run in runs] or \
            [cc.text_fidelity(expected, "")]
        best = next((r for r in reports if r["ok"]), None) or min(
            reports, key=lambda r: (len(r["dropped_marks"]) + r["dropped_spaces"]
                                    + sum(r["foreign_letters"].values()),
                                    abs(len(r["actual"]) - len(expected))))
        _note(session, {"text_fidelity": best})
        return best["ok"], f"{origin}: {cc.describe_fidelity(best)}"

    if kind == "prompt_coverage":
        if session._last_api is None:
            return False, "no api step ran before this assertion"
        payload = session._last_api.get("json") or {}
        field = step.get("unmatched_path") or "unmatched_tokens"
        unmatched = cc.resolve_path(payload, field)
        if unmatched is cc._MISSING or not isinstance(unmatched, list):
            return False, f"json {field} missing or not a list"
        query = step.get("query")
        if query is None:
            from urllib.parse import parse_qs, urlsplit
            url = (getattr(session, "_last_api_request", None) or {}).get("url", "")
            query = (parse_qs(urlsplit(url).query).get(step.get("param") or "q") or [""])[0]
        report = cc.prompt_coverage(query, unmatched)
        minimum = float(step.get("min", step.get("threshold", value or 0.8)))
        _note(session, {"prompt_coverage": report})
        ok = report["tokens"] > 0 and report["coverage"] >= minimum
        return ok, (f"prompt coverage {report['coverage']:.0%} "
                    f"({report['tokens'] - len(report['unmatched'])}/{report['tokens']} "
                    f"tokens, min {minimum:.0%})"
                    + (f"; unmatched: {', '.join(report['unmatched'][:8])}"
                       if report["unmatched"] else ""))

    if kind == "deterministic":
        request = getattr(session, "_last_api_request", None)
        if not request:
            return False, "no api step ran before this assertion"
        runs = max(2, min(int(step.get("runs", value or 3) or 3), 20))
        path = step.get("path") or ""
        ignore = step.get("ignore") or []

        def digest(response: dict) -> str:
            if path or ignore:
                payload = response.get("json")
                if payload is None:
                    return "not-json:" + cc.stable_hash(response.get("body")
                                                        or response.get("text") or "")
                return cc.stable_hash(payload, path=path, ignore=ignore)
            return cc.stable_hash(response.get("body") or response.get("text") or "")

        hashes = [digest(session._last_api)]
        for _ in range(runs - 1):
            response = await session._call_optional(
                "http_request", request["method"], request["url"],
                headers=request.get("headers") or {}, body=request.get("body"),
                timeout_ms=int(request.get("timeout_ms", 15000)))
            hashes.append(digest(response))
        distinct = sorted(set(hashes))
        _note(session, {"hashes": hashes})
        return len(distinct) == 1, (
            f"{runs} identical responses ({distinct[0][:23]}…)" if len(distinct) == 1 else
            f"{len(distinct)} distinct results over {runs} runs: "
            + ", ".join(h[:19] for h in distinct))

    # glyph_fidelity
    text, origin = await content_source(session, step)
    expected = str(step.get("expected") or value or "")
    region = step.get("region") or ""
    if not expected or not region:
        raise SessionRefused("invalid_step", "assert_glyph_fidelity needs 'expected' and "
                                             "'region' (a selector inside the SVG)")
    result = await session._call_optional(
        "glyph_compare", text, region, expected,
        font_path=step.get("font") or step.get("font_path") or "",
        font_family=step.get("font_family") or "",
        scale=int(step.get("scale", 4)),
        artifact_name=step.get("name") or "",
    )
    if result.get("error"):
        _note(session, result)
        return False, f"{origin}: {result['error']}"
    from backend.modules.glyph_raster import glyph_verdict
    ok, reasons = glyph_verdict(
        result["metrics"], min_coverage=step.get("min_coverage"),
        min_window_coverage=step.get("min_window_coverage"),
        max_aspect_delta=step.get("max_aspect_delta"),
        match_word_gaps=step.get("match_word_gaps"))
    _note(session, result)
    m = result["metrics"]
    summary = (f"coverage {m['coverage']:.3f}, window {m['min_window_coverage']:.3f}, "
               f"width Δ {m['aspect_ratio_delta']:.1%}, gaps "
               f"{m['word_gaps_actual']}/{m['word_gaps_reference']}")
    return ok, (f"{origin} {region}: {expected!r} drawn as the reference shapes it ({summary})"
                if ok else f"{origin} {region}: {expected!r} does not match the reference "
                f"shaping — {'; '.join(reasons)} ({summary})")
