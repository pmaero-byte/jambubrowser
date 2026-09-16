"""
Tests for screenshot diffing (backend.modules.visual_diff).

Images are generated in-memory with Pillow so the tests don't depend on
fixture files. The diff must be deterministic and tolerate rendering
jitter (anti-aliasing) without flagging it.
"""
from __future__ import annotations

import base64
import io
import sys

import pytest

from backend.modules.visual_diff import (
    CHANNEL_TOLERANCE,
    compute_visual_change,
    render_diff_image,
    screenshot_hash,
)


def _png(color, size=(64, 48), half_color=None):
    """Encode a solid (or left-half-split) image to base64 PNG."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, color)
    if half_color is not None:
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, size[0] // 2, size[1]], fill=half_color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


BLACK = _png((0, 0, 0))
WHITE = _png((255, 255, 255))
HALF_WHITE = _png((0, 0, 0), half_color=(255, 255, 255))
NEAR_BLACK = _png((CHANNEL_TOLERANCE - 2, CHANNEL_TOLERANCE - 2, CHANNEL_TOLERANCE - 2))


class TestComputeVisualChange:
    def test_identical_images_are_zero(self):
        assert compute_visual_change(BLACK, BLACK) == 0.0

    def test_identical_pixels_different_encoding_is_zero(self):
        # Re-encode the same pixels; the hash fast path doesn't apply, so
        # the pixel path must still report 0.0.
        from PIL import Image

        img = Image.open(io.BytesIO(base64.b64decode(BLACK)))
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=1)
        reencoded = base64.b64encode(buf.getvalue()).decode()
        assert reencoded != BLACK  # different bytes...
        assert compute_visual_change(BLACK, reencoded) == 0.0  # ...same pixels

    def test_completely_different_is_100(self):
        assert compute_visual_change(BLACK, WHITE) == 100.0

    def test_half_changed_is_about_half(self):
        pct = compute_visual_change(BLACK, HALF_WHITE)
        assert pct is not None
        assert 45.0 <= pct <= 55.0

    def test_sub_tolerance_noise_is_ignored(self):
        # Small per-channel deltas (rendering jitter) must not count.
        assert compute_visual_change(BLACK, NEAR_BLACK) == 0.0

    def test_different_sizes_are_compared_after_resize(self):
        small = _png((0, 0, 0), size=(32, 24))
        big = _png((0, 0, 0), size=(64, 48))
        # Same color, different dimensions → 0% after resampling.
        assert compute_visual_change(small, big) == 0.0

    def test_empty_inputs_return_none(self):
        assert compute_visual_change(None, WHITE) is None
        assert compute_visual_change(BLACK, None) is None

    def test_undecodable_input_returns_none(self):
        assert compute_visual_change("bm90LWEtcG5n", BLACK) is None

    def test_missing_pillow_returns_none(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "PIL", None)
        assert compute_visual_change(BLACK, WHITE) is None


class TestScreenshotHash:
    def test_stable_and_content_addressed(self):
        assert screenshot_hash(BLACK) == screenshot_hash(BLACK)
        assert screenshot_hash(BLACK) != screenshot_hash(WHITE)
        assert len(screenshot_hash(BLACK)) == 32

    def test_empty_returns_none(self):
        assert screenshot_hash(None) is None
        assert screenshot_hash("") is None


def _decode_png_b64(payload: str):
    """Decode a base64 PNG to a Pillow image."""
    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")


def _red_pixel_count(img) -> int:
    return sum(1 for px in img.getdata() if px == (255, 0, 0))


class TestRenderDiffImage:
    def test_changed_region_is_painted_red(self):
        rendered = render_diff_image(BLACK, HALF_WHITE)
        assert rendered is not None
        img = _decode_png_b64(rendered)
        assert img.size == (64, 48)  # under the view cap: full resolution
        reds = _red_pixel_count(img)
        # The white half (33 of 64 columns incl. the rectangle edge) is red.
        assert 64 * 48 * 0.45 <= reds <= 64 * 48 * 0.55

    def test_identical_images_have_no_red(self):
        rendered = render_diff_image(BLACK, BLACK)
        assert rendered is not None
        img = _decode_png_b64(rendered)
        assert _red_pixel_count(img) == 0
        # ...but the page is still visible, dimmed.
        assert set(img.getdata()) == {(0, 0, 0)}

    def test_unchanged_pixels_are_dimmed(self):
        rendered = render_diff_image(WHITE, WHITE)
        assert rendered is not None
        pixels = set(_decode_png_b64(rendered).getdata())
        assert pixels == {(int(255 * 0.45),) * 3}

    def test_sub_tolerance_noise_is_not_red(self):
        rendered = render_diff_image(BLACK, NEAR_BLACK)
        assert rendered is not None
        assert _red_pixel_count(_decode_png_b64(rendered)) == 0

    def test_different_sizes_still_render(self):
        small = _png((0, 0, 0), size=(32, 24))
        big = _png((255, 255, 255), size=(64, 48))
        rendered = render_diff_image(small, big)
        assert rendered is not None
        img = _decode_png_b64(rendered)
        assert _red_pixel_count(img) == img.size[0] * img.size[1]

    def test_wide_images_are_capped(self):
        wide = _png((0, 0, 0), size=(1280, 800))
        rendered = render_diff_image(wide, wide)
        assert rendered is not None
        assert _decode_png_b64(rendered).size == (640, 400)

    def test_empty_and_bad_inputs_return_none(self):
        assert render_diff_image(None, WHITE) is None
        assert render_diff_image(BLACK, None) is None
        assert render_diff_image("bm90LWEtcG5n", BLACK) is None

    def test_missing_pillow_returns_none(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "PIL", None)
        assert render_diff_image(BLACK, WHITE) is None
