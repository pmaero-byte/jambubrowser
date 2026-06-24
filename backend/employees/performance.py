"""Performance Inspector — Core Web Vitals and loading performance auditor."""

from .base import BaseEmployee, AuditData


class PerformanceInspector(BaseEmployee):
    name = "Performance Inspector"
    emoji = "⚡"
    max_tokens = 3000

    system_prompt = """You are a web performance auditor specialising in Core Web Vitals and loading
performance. Your job: scan Lighthouse reports, network waterfalls, and console
warnings for performance bottlenecks. Provide specific metrics and actionable fixes.

CHECKLIST — check every item and report findings:

1. CORE WEB VITALS (priority: high/critical)
   - LCP (Largest Contentful Paint): <2.5s good, 2.5-4s needs improvement, >4s poor
     Root causes: slow server response, render-blocking resources, slow resource loads, client-side rendering
   - CLS (Cumulative Layout Shift): <0.1 good, 0.1-0.25 needs improvement, >0.25 poor
     Root causes: images without dimensions, dynamically injected content, web fonts (FOUT/FOIT)
   - INP / TBT (Interaction to Next Paint / Total Blocking Time): <200ms good, 200-500ms needs improvement
     Root causes: long tasks, heavy JS execution, unoptimized event handlers

2. RENDER-BLOCKING RESOURCES (priority: high/medium)
   - CSS in <head> without media queries (blocks rendering)
   - Synchronous JS in <head> without async/defer
   - Third-party scripts injected early in page load
   - CSS @import (causes waterfall blocking)
   - Web fonts that block text rendering

3. RESOURCE SIZE & OPTIMIZATION (priority: medium)
   - Images > 100 KB (unoptimized)
   - JS bundles > 500 KB (code splitting opportunity)
   - CSS > 100 KB (unused CSS)
   - Uncompressed text resources (missing gzip/brotli)
   - Images in wrong format (PNG for photos, no WebP/AVIF)
   - Missing image dimensions (width/height attributes)
   - Missing lazy loading on offscreen images (loading="lazy")
   - Font files > 100 KB (subset fonts)
   - Unused JavaScript / dead code

4. CACHING & DELIVERY (priority: medium)
   - Missing or short Cache-Control headers on static assets
   - No CDN usage for static assets (same-origin serving everything)
   - Missing preconnect/prefetch for critical third-party origins
   - Missing dns-prefetch for external domains
   - Missing preload for critical resources (hero image, fonts)

5. JAVASCRIPT EXECUTION (priority: medium)
   - Large main-thread tasks (>50ms blocking)
   - Missing code splitting (single large bundle)
   - Third-party scripts adding significant overhead
   - Unoptimized polyfills (serving to modern browsers)
   - Missing web workers for CPU-intensive tasks

6. FONT LOADING (priority: low/medium)
   - Web fonts without font-display:swap (FOIT — invisible text during load)
   - Too many font variants loaded
   - Fonts not preloaded

7. DOM SIZE (priority: low/medium)
   - Excessive DOM nodes (>1500 elements — slows style calculation)
   - Deeply nested DOM (>32 levels)

8. MOBILE PERFORMANCE (priority: medium)
   - Resources not optimized for mobile (same large assets)
   - Missing viewport meta tag
   - Tap targets too small for mobile

SEVERITY RUBRIC:
- **critical**: LCP > 4s, multiple render-blocking resources, CLS > 0.25
- **high**: LCP 2.5-4s, render-blocking JS/CSS, TBT > 500ms, CLS 0.1-0.25
- **medium**: Unoptimized images, missing caching, large bundles, font loading issues
- **low**: Minor optimization opportunities, cosmetic improvements

OUTPUT FORMAT:
Return a JSON array. Each finding:
{
  "severity": "critical|high|medium|low|info",
  "category": "lcp|cls|tbt|render-blocking|image-optimization|font-loading|third-party|caching|compression|dom-size|mobile",
  "title": "Short, specific title (e.g. 'LCP 4.2s — hero image blocks rendering')",
  "description": "What the issue is, the measured metric, and the user impact.",
  "fix_suggestion": "Concrete, actionable fix. Include specific resource URLs or code changes.",
  "evidence_snippet": "The metric value, resource URL, or code pattern",
  "score_impact": "e.g. 'LCP -0.8s' or 'CLS -0.15' (estimated improvement)"
}

RULES:
- Only report issues you can confirm from the data provided. Do not hallucinate.
- If the Lighthouse report is missing, note that and base findings on network data.
- If Core Web Vitals all pass, congratulate as an info finding.
- Every finding must include estimated performance impact."""

    def _prepare_data(self, data: AuditData) -> str:
        lines = [f"URL: {data.url}", f"Load time: {data.load_time_ms}ms\n"]

        # Lighthouse report
        lines.append("=== LIGHTHOUSE REPORT ===")
        if data.lighthouse_report:
            cats = data.lighthouse_report.get("categories", {})
            for cat_name in ["performance", "accessibility", "best-practices", "seo"]:
                cat = cats.get(cat_name, {})
                score = cat.get("score") if isinstance(cat, dict) else None
                if score is not None:
                    pct = round(score * 100)
                    label = cat.get("title", cat_name) if isinstance(cat, dict) else cat_name
                    lines.append(f"  {label}: {pct}/100")

            # Individual audits
            audits = data.lighthouse_report.get("audits", {})
            if audits:
                lines.append("\n  --- Key Audits ---")
                key_ids = [
                    "largest-contentful-paint", "cumulative-layout-shift",
                    "total-blocking-time", "speed-index", "interactive",
                    "render-blocking-resources", "uses-responsive-images",
                    "uses-optimized-images", "efficient-animated-content",
                    "unused-css-rules", "unused-javascript",
                    "uses-text-compression", "uses-long-cache-ttl",
                    "server-response-time", "dom-size",
                    "font-display", "offscreen-images",
                ]
                for aid in key_ids:
                    if aid in audits:
                        a = audits[aid]
                        score = a.get("score", "N/A")
                        title = a.get("title", aid)
                        display = a.get("displayValue", "")
                        desc = a.get("description", "")
                        lines.append(f"  {title}: score={score}" +
                                     (f" ({display})" if display else "") +
                                     (f" — {desc[:100]}" if desc else ""))
        else:
            lines.append("  (Lighthouse report not available)")

        # Network requests — filter large ones
        lines.append("\n=== NETWORK REQUESTS (large resources >50KB) ===")
        large = [r for r in data.network_requests
                 if r.get("transfer_size", 0) > 50000 or r.get("resource_type") in ("script", "stylesheet")]
        for r in large[:25]:
            size_kb = round(r.get("transfer_size", 0) / 1024, 1)
            timing = r.get("timing", {})
            ttfb = timing.get("waiting", timing.get("ttfb", 0))
            lines.append(
                f"  [{r.get('resource_type','?')}] {r.get('url','')[:120]} "
                f"→ {r.get('status',0)}, {size_kb}KB, TTFB={ttfb}ms"
            )

        if not large:
            lines.append("  (no large resources detected)")

        # Console warnings
        lines.append("\n=== CONSOLE WARNINGS ===")
        warnings = [l for l in data.console_logs if l.get("level") == "warning"]
        if warnings:
            for w in warnings[:15]:
                lines.append(f"  [warning] {w.get('text','')}")
        else:
            lines.append("  (no console warnings)")

        return "\n".join(lines)
