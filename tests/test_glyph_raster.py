"""Tests for raster glyph fidelity and Pillow baselines (backend/modules/glyph_raster.py)."""

from __future__ import annotations

import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from backend.modules import glyph_raster as gr  # noqa: E402


def _png(bars, width=220, height=60) -> bytes:
    """Black bars on white: a stand-in for glyphs with controllable gaps."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    for x0, x1 in bars:
        draw.rectangle([x0, 10, x1, 50], fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# Two "words" of three glyphs each; the wide gap is the word space.
REFERENCE = [(10, 20), (26, 36), (42, 52), (90, 100), (106, 116), (122, 132)]


class TestCompare:
    def test_identical_passes(self):
        metrics = gr.compare_glyph_images(_png(REFERENCE), _png(REFERENCE))
        ok, reasons = gr.glyph_verdict(metrics)
        assert ok, (metrics, reasons)
        assert metrics["word_gaps_actual"] == metrics["word_gaps_reference"] == 1

    def test_scaled_copy_still_passes(self):
        # Same shapes at twice the size (another raster scale) must not fail.
        big = [(2 * a, 2 * b) for a, b in REFERENCE]
        img = Image.open(io.BytesIO(_png(big, 440, 60)))
        img = img.resize((440, 120))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        metrics = gr.compare_glyph_images(buf.getvalue(), _png(REFERENCE))
        ok, reasons = gr.glyph_verdict(metrics)
        assert ok, (metrics, reasons)

    def test_missing_glyph_fails(self):
        # The fifth glyph vanished and the line kept its width otherwise.
        actual = [b for i, b in enumerate(REFERENCE) if i != 4]
        metrics = gr.compare_glyph_images(_png(actual), _png(REFERENCE))
        ok, reasons = gr.glyph_verdict(metrics)
        assert not ok, metrics
        assert any("coverage" in r for r in reasons)

    def test_dropped_word_space_fails(self):
        joined = [(10, 20), (26, 36), (42, 52), (58, 68), (74, 84), (90, 100)]
        metrics = gr.compare_glyph_images(_png(joined), _png(REFERENCE))
        ok, reasons = gr.glyph_verdict(metrics)
        assert not ok
        assert metrics["word_gaps_actual"] == 0
        assert any("word gap" in r for r in reasons)

    def test_blank_actual_is_an_error(self):
        metrics = gr.compare_glyph_images(_png([]), _png(REFERENCE))
        ok, reasons = gr.glyph_verdict(metrics)
        assert not ok and "no ink" in reasons[0]

    def test_thresholds_are_overridable(self):
        actual = [b for i, b in enumerate(REFERENCE) if i != 4]
        metrics = gr.compare_glyph_images(_png(actual), _png(REFERENCE))
        ok, _ = gr.glyph_verdict(metrics, min_coverage=0.0, min_window_coverage=0.0,
                                 max_aspect_delta=10, match_word_gaps=False)
        assert ok


class TestBaseline:
    def test_create_then_match_then_diff(self, tmp_path):
        base = tmp_path / "pack" / "home.png"
        first = gr.compare_to_baseline(str(base), _png(REFERENCE))
        assert first["baseline_created"] and base.exists()
        same = gr.compare_to_baseline(str(base), _png(REFERENCE), diff_dir=str(tmp_path / "d"))
        assert same["diff_pct"] == 0.0 and "diff_path" not in same
        changed = gr.compare_to_baseline(str(base), _png(REFERENCE[:3]),
                                         diff_dir=str(tmp_path / "d"), name="home")
        assert changed["diff_pct"] > 0
        assert (tmp_path / "d" / "home.diff.png").exists()
        assert (tmp_path / "d" / "home.current.png").exists()

    def test_size_mismatch(self, tmp_path):
        base = tmp_path / "b.png"
        gr.compare_to_baseline(str(base), _png(REFERENCE))
        result = gr.compare_to_baseline(str(base), _png(REFERENCE, width=300))
        assert result["size_mismatch"] and result["diff_pct"] == 100.0


def _svg(text: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 40">'
            '<rect width="200" height="40" fill="white"/>'
            f'<text id="cap" x="6" y="28" font-family="Arial" font-size="20">{text}</text>'
            '</svg>')


async def test_real_chromium_reference_shaping():
    """End to end: Chromium draws both sides; a dropped space or letter fails."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as e:  # pragma: no cover - environment dependent
            pytest.skip(f"chromium unavailable: {e}")
        try:
            context = await browser.new_context()
            reference = await gr.render_reference_text(context, "cafe noir",
                                                       font_family="Arial")
            verdicts = {}
            for drawn in ("cafe noir", "cafenoir", "caf noir"):
                region = await gr.rasterize_svg_region(context, _svg(drawn), "#cap")
                assert region["matches"] == 1 and region["png"]
                metrics = gr.compare_glyph_images(region["png"], reference)
                verdicts[drawn] = gr.glyph_verdict(metrics)[0]
            missing = await gr.rasterize_svg_region(context, _svg("x"), "#nope")
        finally:
            await browser.close()
    assert verdicts == {"cafe noir": True, "cafenoir": False, "caf noir": False}
    assert missing["png"] == b"" and missing["matches"] == 0
