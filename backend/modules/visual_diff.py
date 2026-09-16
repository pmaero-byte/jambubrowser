"""
Screenshot diffing for visual regression detection.

Audit monitors store the screenshot from each run and compare it with the
previous run's: a large share of changed pixels means the page looks
different, whether or not the DOM-based employees found anything new.

Design notes:
- Gray/RGB comparison runs on a downscaled copy (``COMPARE_WIDTH``) so a
  1280×800 PNG diff costs a few milliseconds.
- ``CHANNEL_TOLERANCE`` absorbs anti-aliasing / sub-pixel rendering
  jitter between runs of an unchanged page.
- Pillow is optional at runtime: when it's missing (or an image can't be
  decoded) the functions return ``None`` and monitor runs simply omit the
  visual fields instead of failing.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from typing import Optional

log = logging.getLogger("jambu.visual_diff")

CHANNEL_TOLERANCE = 12
COMPARE_WIDTH = 320
DIFF_VIEW_WIDTH = 640  # rendered diffs are capped at this width (payload sanity)
DIFF_DIM_FACTOR = 0.45  # unchanged pixels are dimmed so red stands out


def screenshot_hash(screenshot_b64: Optional[str]) -> Optional[str]:
    """Fast content hash of a base64 screenshot (no decoding)."""
    if not screenshot_b64:
        return None
    return hashlib.sha256(screenshot_b64.encode("utf-8", "ignore")).hexdigest()[:32]


def compute_visual_change(
    previous_b64: Optional[str], current_b64: Optional[str],
) -> Optional[float]:
    """Percent of pixels that changed between two base64 PNG screenshots.

    Returns:
        ``0.0`` for identical images (hash fast path), a percentage in
        ``(0, 100]`` when diffable, or ``None`` when Pillow is missing,
        inputs are empty, or decoding fails.
    """
    if not previous_b64 or not current_b64:
        return None
    if previous_b64 == current_b64:
        return 0.0

    try:
        import numpy as np
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a declared dep
        return None

    try:
        prev = _load_rgb(Image, np, previous_b64)
        curr = _load_rgb(Image, np, current_b64)
        if prev is None or curr is None:
            return None
        if prev.shape != curr.shape:
            # Different capture size (viewport change) — compare the
            # current image resampled to the previous run's shape.
            curr = np.array(
                Image.fromarray(curr).resize((prev.shape[1], prev.shape[0]))
            )
        # Per-pixel max channel delta; anything above tolerance changed.
        delta = np.abs(prev.astype(np.int16) - curr.astype(np.int16))
        changed = int((delta.max(axis=2) > CHANNEL_TOLERANCE).sum())
        total = int(delta.shape[0] * delta.shape[1])
        if total == 0:
            return None
        return round(changed / total * 100.0, 3)
    except Exception as e:
        log.warning("Visual diff failed: %s", e)
        return None


def _load_rgb(Image, np, screenshot_b64: str):
    """Decode + downscale a base64 image to an RGB numpy array."""
    try:
        img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64))).convert("RGB")
    except Exception as e:
        log.warning("Screenshot decode failed: %s", e)
        return None
    if img.width > COMPARE_WIDTH:
        height = max(1, round(img.height * COMPARE_WIDTH / img.width))
        img = img.resize((COMPARE_WIDTH, height))
    return np.array(img)


def render_diff_image(
    previous_b64: Optional[str], current_b64: Optional[str],
) -> Optional[str]:
    """Base64 PNG visualizing what changed between two screenshots.

    Unchanged pixels are dimmed, changed pixels (same tolerance rule as
    :func:`compute_visual_change`) are painted pure red. Returns None on
    the same conditions as the diff (missing Pillow, empty inputs,
    undecodable images). An identical pair renders the dimmed page with
    no red — i.e. "nothing changed" is visible, not an error.
    """
    if not previous_b64 or not current_b64:
        return None

    try:
        import numpy as np
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a declared dep
        return None

    try:
        prev = _load_full(Image, previous_b64)
        curr = _load_full(Image, current_b64)
        if prev is None or curr is None:
            return None
        if prev.size != curr.size:
            curr = curr.resize(prev.size)
        prev_arr = np.array(prev).astype(np.int16)
        curr_arr = np.array(curr).astype(np.int16)
        changed = (
            np.abs(prev_arr - curr_arr).max(axis=2) > CHANNEL_TOLERANCE
        )
        dimmed = (curr_arr * DIFF_DIM_FACTOR).astype(np.uint8)
        dimmed[changed] = (255, 0, 0)
        out = Image.fromarray(dimmed)
        buf = io.BytesIO()
        out.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log.warning("Diff render failed: %s", e)
        return None


def _load_full(Image, screenshot_b64: str):
    """Decode a base64 image to RGB, capping width for payload sanity."""
    try:
        img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64))).convert("RGB")
    except Exception as e:
        log.warning("Screenshot decode failed: %s", e)
        return None
    if img.width > DIFF_VIEW_WIDTH:
        height = max(1, round(img.height * DIFF_VIEW_WIDTH / img.width))
        img = img.resize((DIFF_VIEW_WIDTH, height))
    return img
