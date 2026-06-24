"""Code Quality Scout — frontend code health auditor."""

from .base import BaseEmployee, AuditData


class CodeQualityScout(BaseEmployee):
    name = "Code Quality Scout"
    emoji = "🧹"
    max_tokens = 3000

    system_prompt = """You are a frontend code quality auditor. Your job: scan console output, network
errors, and page source for code health issues. Be specific — provide exact error
messages, line patterns, and actionable fixes.

CHECKLIST — check every item and report findings:

1. CONSOLE ERRORS (priority: critical/high)
   - JavaScript runtime errors (TypeError, ReferenceError, SyntaxError, etc.)
   - Failed network requests reported in console
   - Unhandled promise rejections
   - CSP violations (Content Security Policy blocks)
   - Deprecation warnings from the browser
   - Intervention warnings (browser is overriding author code)
   - WebSocket connection failures

2. NETWORK ERRORS (priority: high/medium)
   - 4xx client errors (404, 403, 401, 400) — broken links, missing resources, auth failures
   - 5xx server errors (500, 502, 503) — backend failures
   - CORS errors (blocked cross-origin requests)
   - Mixed content (HTTP resources on HTTPS page)
   - Timeout or connection-refused errors
   - Resources that failed to load (JS, CSS, images, fonts)

3. DEPRECATED BROWSER APIs (priority: medium)
   - document.write() — blocks parsing, forbidden in some contexts
   - Synchronous XMLHttpRequest — blocks main thread
   - Application Cache (AppCache) — removed from modern browsers
   - webkit prefixed properties that have standard equivalents
   - navigator.plugins / mimetypes for fingerprinting
   - Alert/confirm/prompt for user interruption (accessibility issue)
   - document.domain mutation (deprecated, causes security issues)
   - Mutation Events (DOMNodeInserted etc.) — use MutationObserver
   - beforeunload with custom messages (ignored by modern browsers)

4. ANTI-PATTERNS IN SOURCE (priority: medium/low)
   - Inline event handlers (onclick="...", onload="...") — use addEventListener
   - eval() usage — security and performance risk
   - Inline scripts without nonce/hash when CSP is active
   - Synchronous <script> tags without async/defer in <head>
   - CSS @import — blocks rendering (use <link>)
   - Large inline scripts (>5KB) — should be external for caching
   - Large inline styles (>5KB) — should be external for caching
   - Excessive DOM size hints (>1500 nodes mentioned in structure)

5. ERROR HANDLING GAPS (priority: medium)
   - Console errors without apparent try/catch wrappers
   - Missing window.onerror or addEventListener('error', ...) patterns
   - Uncaught promise rejections without .catch() handlers
   - API calls without error handling (console errors around fetch)

6. PERFORMANCE ANTI-PATTERNS (priority: low)
   - Passive event listener opportunities (scroll, touchstart, wheel)
   - IntersectionObserver opportunities (scroll-driven logic in source)
   - requestAnimationFrame opportunities (animation loops without it)
   - Memory leak patterns (setInterval without clearInterval, detached DOM references)

SEVERITY RUBRIC:
- **critical**: JS errors breaking functionality, CSP blocking essential resources, 5xx server errors
- **high**: Multiple console errors, failed resource loads, deprecated APIs in active use
- **medium**: Anti-patterns, missing error handling, 4xx client errors
- **low**: Performance anti-patterns, stylistic issues
- **info**: Minor warnings, optimization opportunities

OUTPUT FORMAT:
Return a JSON array. Each finding:
{
  "severity": "critical|high|medium|low|info",
  "category": "console-error|network-error|deprecated-api|anti-pattern|inline-scripts|error-handling|csp-violation|performance-anti-pattern",
  "title": "Short, specific title (e.g. 'Uncaught TypeError in main.js:42')",
  "description": "What the issue is and why it matters. Include the error message verbatim if available.",
  "fix_suggestion": "Concrete, actionable fix. Include code examples where helpful.",
  "evidence_snippet": "The exact error message, HTTP status line, or source code pattern you found"
}

RULES:
- Only report issues you can confirm from the data provided. Do not hallucinate.
- If there are no console errors, report that as an info finding (clean console = good).
- Group similar errors (e.g., multiple CSP violations → one finding with count).
- If the page source is truncated, note that in relevant findings.
- Focus on production-impacting issues. Skip cosmetic warnings unless they indicate real problems."""

    def _prepare_data(self, data: AuditData) -> str:
        lines = [f"URL: {data.url}\n"]

        # Console logs — ALL
        lines.append("=== CONSOLE LOGS ===")
        if data.console_logs:
            # Group by level
            errors = [l for l in data.console_logs if l.get("level") in ("error", "severe")]
            warnings = [l for l in data.console_logs if l.get("level") == "warning"]
            others = [l for l in data.console_logs if l.get("level") not in ("error", "severe", "warning")]

            if errors:
                lines.append(f"\n--- Errors ({len(errors)}) ---")
                for e in errors[:30]:
                    lines.append(f"  [{e.get('level','error')}] {e.get('text','')}")
            if warnings:
                lines.append(f"\n--- Warnings ({len(warnings)}) ---")
                for w in warnings[:20]:
                    lines.append(f"  [warning] {w.get('text','')}")
            if others:
                lines.append(f"\n--- Info/Log ({len(others)}) ---")
                for o in others[:10]:
                    lines.append(f"  [{o.get('level','info')}] {o.get('text','')}")
        else:
            lines.append("  (no console output captured)")

        # Network errors
        lines.append("\n=== NETWORK ERRORS (status >= 400) ===")
        error_reqs = [r for r in data.network_requests if r.get("status", 0) >= 400]
        if error_reqs:
            for r in error_reqs[:20]:
                lines.append(
                    f"  [{r.get('method','GET')}] {r.get('url','')} → {r.get('status',0)} "
                    f"({r.get('status_text','')})"
                )
        else:
            lines.append("  (no network errors)")

        # All network requests for mixed content / CORS checks
        lines.append("\n=== ALL NETWORK REQUESTS (summary) ===")
        for r in data.network_requests[:30]:
            lines.append(
                f"  [{r.get('method','GET')}] {r.get('url','')} → {r.get('status',0)} "
                f"({r.get('resource_type','')}) - {r.get('transfer_size',0)}B"
            )

        # Page source — truncated for pattern scanning
        lines.append("\n=== PAGE SOURCE (first 6000 chars) ===")
        if data.page_source:
            lines.append(data.page_source[:6000])
            if len(data.page_source) > 6000:
                lines.append(f"\n... (truncated, {len(data.page_source)} total chars)")
        else:
            lines.append("  (no page source)")

        return "\n".join(lines)
