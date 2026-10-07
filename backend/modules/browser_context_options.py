"""Deterministic viewport and device emulation for browser-agent runs.

Two problems this module exists to solve:

1. **The default viewport was random.** Context options came from
   :mod:`backend.modules.fingerprint_rotator`, which picks a fingerprint per
   session. Two runs of the same flow therefore rendered at different sizes
   (observed 1680x1050 vs 1280x800), so any assertion that depended on layout --
   "is the Skip button reachable at 390px?", "does this element wrap?" -- was not
   reproducible. Fingerprint rotation is right for *identity* and wrong for
   *geometry*. Geometry now defaults to :data:`DEFAULT_VIEWPORT` and is only
   varied when a caller asks.

2. **Viewport only worked inside a matrix.** ``/browser/sessions`` and ``/run``
   accepted no viewport at all, and ``viewport``/``width``/``height`` were
   silently ignored, so a persistent session could never be taken to a phone
   width. Every entry point now normalises the same set of knobs.

Everything here is pure: :func:`normalize_context_options` is a function over
dicts and can be tested without a browser.
"""
from __future__ import annotations

from typing import Any, Optional

# The deterministic default. Chosen to fit the common laptop breakpoint and to
# be wide enough that desktop layouts do not collapse, tall enough to show a
# results table plus a viewport.
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}

# Playwright ``new_context`` keys we accept from a caller, mapped to the
# normalization they need. Anything not listed is not silently dropped -- it is
# passed through untouched (storage_state, record_har_path, and so on).
PASSTHROUGH_CONTEXT_KEYS = frozenset({
    "storage_state", "storage_state_path", "record_har_path", "record_har_content",
    "record_video_dir", "record_video_size", "extra_http_headers", "base_url",
    "ignore_https_errors", "service_workers", "accept_downloads", "bypass_csp",
    "java_script_enabled", "offline", "permissions", "proxy", "client_certificates",
    "http_credentials", "strict_selectors", "user_agent", "user_agent_metadata",
    "geolocation", "locale", "timezone_id", "device_scale_factor", "has_touch",
    "is_mobile", "color_scheme", "reduced_motion", "forced_colors", "contrast",
    "screen", "expect_downloads",
})

# Coercible scalars. Values arrive from JSON (and from CLI flags) as strings,
# so "1280" has to become 1280 or Playwright rejects the whole context.
_BOOL_CONTEXT_KEYS = frozenset({
    "has_touch", "is_mobile", "java_script_enabled", "ignore_https_errors",
    "offline", "bypass_csp", "accept_downloads", "strict_selectors",
})
_INT_CONTEXT_KEYS = frozenset({"device_scale_factor"})

_DEVICE_PRESETS: dict[str, dict[str, Any]] = {
    # Viewport sizes mirror the Playwright device descriptors so a run on a
    # phone preset matches what a real phone browser would lay out.
    "desktop": {
        "viewport": {"width": 1440, "height": 900},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    },
    "laptop": {
        "viewport": {"width": 1280, "height": 800},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    },
    "tablet": {
        "viewport": {"width": 834, "height": 1112},
        "device_scale_factor": 2,
        "is_mobile": True,
        "has_touch": True,
    },
    "mobile": {
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
    "iphone_13": {
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
    "pixel_5": {
        "viewport": {"width": 393, "height": 851},
        "device_scale_factor": 2.75,
        "is_mobile": True,
        "has_touch": True,
    },
}

DEVICE_PRESETS = dict(_DEVICE_PRESETS)


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer, got {value!r}") from None


def _as_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{field} must be a boolean, got {value!r}")


def parse_viewport(viewport: Any, *, field: str = "viewport") -> dict[str, int]:
    """Normalize a viewport given as a dict or as ``"1280x800"`` / ``[w, h]``."""
    if viewport is None:
        raise ValueError(f"{field} is empty")
    if isinstance(viewport, str):
        text = viewport.strip().lower().replace("×", "x")
        if "x" not in text:
            raise ValueError(f"{field} must look like 1280x800, got {viewport!r}")
        width_s, _, height_s = text.partition("x")
        return {"width": _as_int(width_s, field), "height": _as_int(height_s, field)}
    if isinstance(viewport, (list, tuple)) and len(viewport) == 2:
        return {"width": _as_int(viewport[0], field), "height": _as_int(viewport[1], field)}
    if isinstance(viewport, dict):
        if "width" in viewport and "height" in viewport:
            return {"width": _as_int(viewport["width"], field),
                    "height": _as_int(viewport["height"], field)}
        # Accept the width/height spelling used by viewport_width/viewport_height.
        if "w" in viewport and "h" in viewport:
            return {"width": _as_int(viewport["w"], field),
                    "height": _as_int(viewport["h"], field)}
    raise ValueError(f"{field} must be {{'width': w, 'height': h}} or 'WxH', got {viewport!r}")


def _normalize_screen(value: Any) -> Optional[dict[str, int]]:
    if value is None:
        return None
    if isinstance(value, str):
        return parse_viewport(value, field="screen")
    if isinstance(value, dict):
        return parse_viewport(value, field="screen")
    return None


def normalize_context_options(
    options: Optional[dict] = None,
    *,
    viewport: Any = None,
    device: Optional[str] = None,
    viewport_width: Any = None,
    viewport_height: Any = None,
    is_mobile: Any = None,
    has_touch: Any = None,
    color_scheme: Any = None,
    reduced_motion: Any = None,
    device_scale_factor: Any = None,
    screen: Any = None,
    viewport_matrix: bool = False,
) -> dict[str, Any]:
    """Build the Playwright ``new_context`` options for a run or a session.

    Precedence, lowest to highest: fingerprint defaults, ``device`` preset,
    ``viewport``, then the individual scalar knobs. An explicit scalar always
    beats a preset, so ``device="mobile", viewport="1024x768"`` is a tablet-
    sized run that still reports touch.

    ``viewport_matrix=True`` (the ``/matrix`` endpoint) leaves ``viewport`` unset
    when the caller did not name one, because the matrix supplies the geometry
    per variant and an unset default is the honest thing to pass.
    """
    opts: dict[str, Any] = dict(options or {})

    preset_name = (device or "").strip().lower()
    preset = _DEVICE_PRESETS.get(preset_name)
    if preset_name and preset is None:
        known = ", ".join(sorted(_DEVICE_PRESETS))
        raise ValueError(f"unknown device {device!r}; known devices: {known}")
    if preset:
        for key, value in preset.items():
            opts[key] = dict(value) if isinstance(value, dict) else value

    # width/height spelling -- only applied when it can form a complete pair,
    # otherwise the preset (or default) viewport is left alone rather than
    # half-overwritten with a width and no height.
    if viewport_width is not None or viewport_height is not None:
        current = opts.get("viewport") or DEFAULT_VIEWPORT
        opts["viewport"] = {
            "width": _as_int(viewport_width if viewport_width is not None
                             else current.get("width", DEFAULT_VIEWPORT["width"]),
                             "viewport_width"),
            "height": _as_int(viewport_height if viewport_height is not None
                              else current.get("height", DEFAULT_VIEWPORT["height"]),
                              "viewport_height"),
        }

    if viewport is not None:
        opts["viewport"] = parse_viewport(viewport)

    if device_scale_factor is not None:
        opts["device_scale_factor"] = _as_int(device_scale_factor, "device_scale_factor")
    if is_mobile is not None:
        opts["is_mobile"] = _as_bool(is_mobile, "is_mobile")
    if has_touch is not None:
        opts["has_touch"] = _as_bool(has_touch, "has_touch")
    if color_scheme is not None:
        scheme = str(color_scheme).strip().lower()
        if scheme not in ("light", "dark", "no-preference", "null"):
            raise ValueError(f"color_scheme must be light/dark/no-preference, got {color_scheme!r}")
        # "null" is Playwright's spelling for following the OS preference.
        opts["color_scheme"] = None if scheme == "null" else scheme
    if reduced_motion is not None:
        motion = str(reduced_motion).strip().lower()
        if motion not in ("reduce", "no-preference", "null"):
            raise ValueError(f"reduced_motion must be reduce/no-preference, got {reduced_motion!r}")
        opts["reduced_motion"] = None if motion == "null" else motion
    if screen is not None:
        normalized_screen = _normalize_screen(screen)
        if normalized_screen:
            opts["screen"] = normalized_screen

    # Geometry is what makes a run reproducible, so it is always explicit --
    # unless this is a matrix variant that carries its own.
    if not viewport_matrix or opts.get("viewport") is not None:
        opts.setdefault("viewport", dict(DEFAULT_VIEWPORT))
    # ...and a mobile-ish context has to agree with itself: Playwright rejects
    # is_mobile with a non-null device_scale_factor mismatch in some builds, and
    # a touch viewport without has_touch makes tap-only UI untestable.
    if opts.get("is_mobile") and "has_touch" not in opts:
        opts["has_touch"] = True
    if opts.get("has_touch") and "is_mobile" not in opts:
        opts["is_mobile"] = False

    return opts
