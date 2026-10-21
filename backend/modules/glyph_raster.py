"""Raster comparisons: glyph fidelity against a reference shaper, and baselines.

Text drawn as outlines (comic lettering, PDF-style SVG) has no characters
left to compare, so a renderer can drop a vowel sign or a word space and
still report the right string. This module compares *pixels* instead:

* :func:`render_reference_text` asks Chromium (HarfBuzz shaping) to draw the
  expected text in the font the product claims to use.
* :func:`rasterize_svg_region` draws the product's own SVG and clips it to the
  lettering region.
* :func:`compare_glyph_images` normalises both to the ink bounding box and
  scores their overlap, and counts word gaps from the column profile.

Visual baselines (:func:`compare_to_baseline`) use the same Pillow tooling.
Everything here is deterministic and needs no LLM.
"""
from __future__ import annotations

import base64
import html
import io
import os
from typing import Optional

try:  # Pillow is optional for the rest of Jambu; these checks need it.
    from PIL import Image, ImageChops, ImageFilter
except ImportError:  # pragma: no cover - exercised only without Pillow
    Image = None  # type: ignore[assignment]

NORM_HEIGHT = 96


def _require_pillow() -> None:
    if Image is None:
        raise RuntimeError("Pillow is required for raster checks (pip install Pillow)")


def _load(png) -> "Image.Image":
    _require_pillow()
    if isinstance(png, str):
        png = base64.b64decode(png)
    return Image.open(io.BytesIO(png)).convert("L")


def ink_mask(img: "Image.Image") -> "Image.Image":
    """Binary mask of ink (dark) pixels; robust to paper texture.

    The cut sits halfway between the 2nd and 98th brightness percentiles,
    so grain on a tinted paper background stays background.
    """
    hist = img.histogram()
    total = sum(hist)

    def percentile(p: float) -> int:
        acc = 0
        for value, count in enumerate(hist):
            acc += count
            if acc >= total * p:
                return value
        return 255

    lo, hi = percentile(0.02), percentile(0.98)
    if hi - lo < 40:  # no contrast: blank region
        return Image.new("1", img.size, 0)
    cut = (lo + hi) // 2
    return img.point(lambda v: 255 if v < cut else 0).convert("1")


def _normalise(mask: "Image.Image", height: int = NORM_HEIGHT):
    box = mask.getbbox()
    if box is None:
        return None
    crop = mask.crop(box)
    w, h = crop.size
    width = max(1, round(w * height / h))
    return crop.convert("L").resize((width, height), Image.LANCZOS).point(
        lambda v: 255 if v >= 128 else 0)


def word_gaps(norm: "Image.Image", min_gap_ratio: float = 0.22) -> int:
    """Count column gaps at least ``min_gap_ratio`` × height wide between ink."""
    width, height = norm.size
    pixels = norm.load()
    cols = [any(pixels[x, y] for y in range(height)) for x in range(width)]
    gaps = 0
    run = 0
    seen_ink = False
    for filled in cols:
        if filled:
            if seen_ink and run >= min_gap_ratio * height:
                gaps += 1
            seen_ink = True
            run = 0
        else:
            run += 1
    return gaps


def _hits(a: "Image.Image", b: "Image.Image", slack: int) -> "Image.Image":
    """Pixels of ``a``'s ink that land within ``slack`` px of ``b``'s ink."""
    grown = b.filter(ImageFilter.MaxFilter(2 * slack + 1)) if slack else b
    return ImageChops.multiply(a, grown)


def _column_counts(img: "Image.Image") -> list:
    width, height = img.size
    px = img.load()
    return [sum(1 for y in range(height) if px[x, y]) for x in range(width)]


def _coverage(a: "Image.Image", b: "Image.Image", slack: int, window: int) -> tuple:
    """(overall, worst-window) share of ``a``'s ink covered by ``b``'s ink."""
    total = _column_counts(a)
    hit = _column_counts(_hits(a, b, slack))
    overall = sum(hit) / sum(total) if sum(total) else 0.0
    worst = 1.0
    height = a.size[1]
    for x0 in range(0, max(1, len(total) - window + 1), 4):
        ink = sum(total[x0:x0 + window])
        if ink < 0.02 * window * height:  # blank stretch, nothing to cover
            continue
        worst = min(worst, sum(hit[x0:x0 + window]) / ink)
    return overall, worst


def compare_glyph_images(actual_png, reference_png, *, slack: int = 2,
                         height: int = NORM_HEIGHT) -> dict:
    """Score how closely rendered lettering matches the reference shaping.

    Both images are reduced to ink and cropped to their ink box; the actual
    image is then stretched onto the reference's normalised frame, so a
    heavier stroke weight or a slightly different size does not matter.

    * ``coverage``: share of reference ink within ``slack`` px of actual
      ink. A dropped vowel sign or glyph leaves reference ink uncovered.
    * ``min_window_coverage``: the worst coverage over a sliding window half
      a line-height wide, so one missing mark in a long line still shows.
    * ``aspect_ratio_delta``: relative width change of the ink box; a
      dropped word space or glyph narrows the line.
    * ``word_gaps_*``: column gaps wider than ~a quarter of the line height.
    """
    a_mask = ink_mask(_load(actual_png))
    r_mask = ink_mask(_load(reference_png))
    a_box, r_box = a_mask.getbbox(), r_mask.getbbox()
    if a_box is None or r_box is None:
        return {"similarity": 0.0, "coverage": 0.0, "min_window_coverage": 0.0,
                "error": "no ink in " + ("actual" if a_box is None else "reference")}
    a_crop, r_crop = a_mask.crop(a_box), r_mask.crop(r_box)
    width = max(1, round(r_crop.size[0] * height / r_crop.size[1]))

    def fit(mask):
        return mask.convert("L").resize((width, height), Image.LANCZOS).point(
            lambda v: 255 if v >= 128 else 0)

    a, r = fit(a_crop), fit(r_crop)
    window = max(8, height // 2)
    coverage, worst = _coverage(r, a, slack, window)
    precision, _ = _coverage(a, r, slack, window)
    f1 = (2 * precision * coverage / (precision + coverage)) if precision + coverage else 0.0
    a_aspect = a_crop.size[0] / a_crop.size[1]
    r_aspect = r_crop.size[0] / r_crop.size[1]
    return {
        "similarity": round(f1, 4),
        "coverage": round(coverage, 4),
        "precision": round(precision, 4),
        "min_window_coverage": round(worst, 4),
        "actual_aspect": round(a_aspect, 3),
        "reference_aspect": round(r_aspect, 3),
        "aspect_ratio_delta": round(abs(a_aspect - r_aspect) / max(r_aspect, 1e-6), 4),
        "word_gaps_actual": word_gaps(_normalise(a_crop, height)),
        "word_gaps_reference": word_gaps(_normalise(r_crop, height)),
    }


GLYPH_THRESHOLDS = {"min_coverage": 0.93, "min_window_coverage": 0.80,
                    "max_aspect_delta": 0.12, "match_word_gaps": True}


def glyph_verdict(metrics: dict, **overrides) -> tuple:
    """Turn :func:`compare_glyph_images` metrics into ``(ok, reasons)``.

    Default thresholds were calibrated on Noto Sans Tamil outlines against
    Chromium's shaping of the same font: correctly drawn words scored
    coverage >= 0.97 and window coverage >= 0.84; words with a dropped
    vowel sign scored <= 0.96 / 0.78. They are heuristics, so every call
    can override them.
    """
    limits = dict(GLYPH_THRESHOLDS)
    limits.update({k: v for k, v in overrides.items() if v is not None})
    if metrics.get("error"):
        return False, [metrics["error"]]
    reasons = []
    if metrics["coverage"] < limits["min_coverage"]:
        reasons.append(f"reference ink coverage {metrics['coverage']:.3f} < "
                       f"{limits['min_coverage']}")
    if metrics["min_window_coverage"] < limits["min_window_coverage"]:
        reasons.append(f"worst-window coverage {metrics['min_window_coverage']:.3f} < "
                       f"{limits['min_window_coverage']} (a glyph or mark is missing "
                       f"or different)")
    if metrics["aspect_ratio_delta"] > limits["max_aspect_delta"]:
        reasons.append(f"line width differs by {metrics['aspect_ratio_delta']:.1%} "
                       f"(> {limits['max_aspect_delta']:.0%})")
    if limits["match_word_gaps"] and metrics["word_gaps_actual"] != metrics["word_gaps_reference"]:
        reasons.append(f"{metrics['word_gaps_actual']} word gap(s) drawn, "
                       f"{metrics['word_gaps_reference']} expected")
    return (not reasons), reasons


def _font_face(font_path: str, family: str) -> str:
    if not font_path:
        return ""
    with open(font_path, "rb") as handle:
        data = base64.b64encode(handle.read()).decode("ascii")
    fmt = "opentype" if font_path.lower().endswith(".otf") else "truetype"
    return (f"@font-face{{font-family:'{family}';"
            f"src:url(data:font/{fmt};base64,{data}) format('{fmt}');}}")


async def render_reference_text(context, text: str, *, font_path: str = "",
                                font_family: str = "", px: int = 96) -> bytes:
    """PNG of ``text`` shaped by Chromium in the given font (black on white)."""
    family = "JambuReference" if font_path else (font_family or "sans-serif")
    page = await context.new_page()
    try:
        await page.set_content(
            "<!doctype html><meta charset=utf-8><style>"
            + _font_face(font_path, family)
            + "html,body{margin:0;background:#fff}"
            + f"#t{{display:inline-block;padding:{px // 2}px;font:{px}px '{family}',"
            + "sans-serif;color:#000;white-space:pre;line-height:1.6}</style>"
            + f"<span id=t>{html.escape(text)}</span>")
        await page.evaluate("document.fonts.ready.then(() => true)")
        return await page.locator("#t").screenshot()
    finally:
        await page.close()


async def rasterize_svg_region(context, svg: str, region_selector: str, *,
                               scale: int = 4, pad: float = 2.0) -> dict:
    """Draw ``svg`` and clip to the union box of ``region_selector`` matches.

    Returns ``{"png": bytes, "box": [x, y, w, h], "matches": n}``; ``png`` is
    empty when nothing matches. Rendered in a scratch page at ``scale`` so
    small lettering has enough pixels to compare.
    """
    page = await context.new_page()
    try:
        await page.set_viewport_size({"width": 400, "height": 300})
        await page.set_content("<!doctype html><meta charset=utf-8>"
                               "<style>html,body{margin:0;background:#fff}</style>"
                               f"<div id=host>{svg}</div>")
        box = await page.evaluate(
            """([sel, scale]) => {
                const svg = document.querySelector('#host svg');
                if (!svg) return null;
                const vb = svg.viewBox && svg.viewBox.baseVal;
                const w = (vb && vb.width) || svg.getBBox().width;
                const h = (vb && vb.height) || svg.getBBox().height;
                svg.setAttribute('width', w * scale);
                svg.setAttribute('height', h * scale);
                const els = Array.from(svg.querySelectorAll(sel));
                if (!els.length) return {matches: 0};
                let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (!r.width && !r.height) continue;
                    x0 = Math.min(x0, r.left); y0 = Math.min(y0, r.top);
                    x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom);
                }
                return {matches: els.length, x: x0, y: y0, w: x1 - x0, h: y1 - y0,
                        pw: w * scale, ph: h * scale};
            }""", [region_selector, scale])
        if not box or not box.get("matches") or not box.get("w"):
            return {"png": b"", "box": None, "matches": (box or {}).get("matches", 0)}
        await page.set_viewport_size({"width": int(box["pw"]) + 1,
                                      "height": int(box["ph"]) + 1})
        p = pad * scale
        clip = {"x": max(0.0, box["x"] - p), "y": max(0.0, box["y"] - p),
                "width": box["w"] + 2 * p, "height": box["h"] + 2 * p}
        png = await page.screenshot(clip=clip)
        return {"png": png, "matches": box["matches"],
                "box": [round(v / scale, 2) for v in
                        (box["x"], box["y"], box["w"], box["h"])]}
    finally:
        await page.close()


def compare_to_baseline(baseline_path: str, current_png, *, tolerance: int = 16,
                        diff_dir: Optional[str] = None, name: str = "") -> dict:
    """Pillow comparison of a capture against a stored PNG baseline.

    A channel difference at or below ``tolerance`` counts as equal. A
    missing baseline is created (``baseline_created``). On a difference the
    current frame and a red-on-grey diff image are written to ``diff_dir``.
    """
    _require_pillow()
    if isinstance(current_png, str):
        current_png = base64.b64decode(current_png)
    if not os.path.exists(baseline_path):
        os.makedirs(os.path.dirname(baseline_path) or ".", exist_ok=True)
        with open(baseline_path, "wb") as handle:
            handle.write(current_png)
        return {"baseline_created": True, "baseline_path": baseline_path,
                "diff_pct": 0.0}
    base = Image.open(baseline_path).convert("RGB")
    cur = Image.open(io.BytesIO(current_png)).convert("RGB")
    result: dict = {"baseline_path": baseline_path, "engine": "pillow",
                    "baseline_size": list(base.size), "current_size": list(cur.size)}
    if base.size != cur.size:
        result.update({"size_mismatch": True, "diff_pct": 100.0,
                       "diff_pixels": base.size[0] * base.size[1]})
        differing = None
    else:
        delta = ImageChops.difference(base, cur).convert("L")
        differing = delta.point(lambda v: 255 if v > tolerance else 0)
        count = sum(1 for v in differing.getdata() if v)
        total = base.size[0] * base.size[1]
        result.update({"diff_pixels": count, "diff_pct": 100.0 * count / max(total, 1)})
    if result["diff_pct"] > 0 and diff_dir:
        os.makedirs(diff_dir, exist_ok=True)
        stem = name or os.path.splitext(os.path.basename(baseline_path))[0]
        current_path = os.path.join(diff_dir, f"{stem}.current.png")
        cur.save(current_path)
        result["current_path"] = current_path
        if differing is not None:
            overlay = Image.composite(Image.new("RGB", cur.size, (220, 0, 0)),
                                      cur.convert("L").convert("RGB"), differing)
            diff_path = os.path.join(diff_dir, f"{stem}.diff.png")
            overlay.save(diff_path)
            result["diff_path"] = diff_path
    return result
