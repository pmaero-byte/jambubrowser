"""Findings post-processing — deduplication, business impact scoring, grouping.

After all employees produce findings, this module:
1. Deduplicates related findings (7 missing headers → 1 group)
2. Adds business impact scoring (user_impact, revenue_impact, fix_effort)
3. Groups findings into actionable fix bundles
4. Generates specific code fixes based on the product's tech stack
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.employees.base import Finding, Severity

log = logging.getLogger("jambu.findings")


@dataclass
class BusinessImpact:
    """Business impact assessment for a finding."""
    user_impact: str  # "all users", "mobile users", "screen reader users", etc.
    revenue_impact: str  # "direct", "indirect", "none"
    fix_effort: str  # "5 minutes", "1 hour", "1 day"
    priority_score: int  # 1-10, higher = more important
    reasoning: str  # Why this priority


@dataclass
class EnhancedFinding(Finding):
    """Finding with business impact and code fix."""
    business_impact: Optional[BusinessImpact] = None
    code_fix: Optional[str] = None
    fix_group: Optional[str] = None  # Groups related findings


@dataclass
class FixGroup:
    """Group of related findings that can be fixed together."""
    id: str
    title: str
    description: str
    findings: list[EnhancedFinding]
    total_impact: str
    fix_effort: str
    code_fix: str


# Deduplication rules: category patterns that should be grouped
DEDUP_GROUPS = {
    "security_headers": {
        "categories": ["headers", "csp", "hsts", "x-content-type-options", "x-frame-options",
                       "referrer-policy", "permissions-policy", "coop", "corp"],
        "title": "Missing Security Headers",
        "description": "Multiple security headers are missing. These can be added in one configuration change.",
    },
    "form_accessibility": {
        "categories": ["forms", "labels", "aria"],
        "title": "Form Accessibility Issues",
        "description": "Form inputs lack proper labels and ARIA attributes.",
    },
    "meta_tags": {
        "categories": ["meta-description", "og-tags", "twitter-card", "title"],
        "title": "Meta Tag Issues",
        "description": "SEO and social sharing meta tags need improvement.",
    },
    "performance": {
        "categories": ["lcp", "cls", "tbt", "render-blocking", "caching", "compression"],
        "title": "Performance Issues",
        "description": "Page load performance can be improved.",
    },
    "accessibility": {
        "categories": ["contrast", "focus", "keyboard", "landmarks", "alt-text", "links"],
        "title": "Accessibility Issues",
        "description": "The page has accessibility barriers for users with disabilities.",
    },
    "deprecated_apis": {
        "categories": ["deprecated-api", "third-party"],
        "title": "Deprecated API Usage",
        "description": "Console warnings indicate deprecated browser API usage.",
    },
    "semantic_html": {
        "categories": ["content", "navigation", "landmarks"],
        "title": "Semantic HTML Structure",
        "description": "Page structure could be improved with semantic HTML elements.",
    },
    "service_worker": {
        "categories": ["error-handling", "anti-pattern", "loading"],
        "title": "Service Worker & Loading Patterns",
        "description": "Service worker and loading patterns need attention.",
    },
    "info_disclosure": {
        "categories": ["info-disclosure"],
        "title": "Information Disclosure",
        "description": "Server headers reveal infrastructure details.",
    },
}


def deduplicate_findings(findings: list[Finding]) -> list[EnhancedFinding]:
    """Deduplicate and enhance findings with business impact."""
    enhanced = []

    for f in findings:
        ef = EnhancedFinding(
            id=f.id,
            employee=f.employee,
            severity=f.severity,
            category=f.category,
            title=f.title,
            description=f.description,
            fix_suggestion=f.fix_suggestion,
            evidence_snippet=f.evidence_snippet,
            wcag_criterion=f.wcag_criterion,
            score_impact=f.score_impact,
            business_impact=_assess_business_impact(f),
            code_fix=_generate_code_fix(f),
            fix_group=_get_fix_group(f),
        )
        enhanced.append(ef)

    return enhanced


def group_findings(findings: list[EnhancedFinding]) -> list[FixGroup]:
    """Group related findings into actionable fix bundles."""
    groups: dict[str, list[EnhancedFinding]] = {}

    for f in findings:
        group_id = f.fix_group or f.category
        if group_id not in groups:
            groups[group_id] = []
        groups[group_id].append(f)

    fix_groups = []
    for group_id, group_findings in groups.items():
        if group_id in DEDUP_GROUPS:
            rule = DEDUP_GROUPS[group_id]
            title = rule["title"]
            description = rule["description"]
        else:
            title = group_findings[0].title
            description = group_findings[0].description

        # Aggregate impact
        max_severity = max(group_findings, key=lambda f: _severity_rank(f.severity))
        total_effort = _estimate_group_effort(group_findings)

        # Combine code fixes
        code_fixes = [f.code_fix for f in group_findings if f.code_fix]
        combined_fix = "\n\n".join(code_fixes) if code_fixes else None

        fg = FixGroup(
            id=group_id,
            title=title,
            description=description,
            findings=group_findings,
            total_impact=max_severity.severity.value,
            fix_effort=total_effort,
            code_fix=combined_fix or "See individual findings for specific fixes.",
        )
        fix_groups.append(fg)

    # Sort by priority (critical first)
    fix_groups.sort(key=lambda g: _severity_rank(
        max(g.findings, key=lambda f: _severity_rank(f.severity)).severity
    ), reverse=True)

    return fix_groups


def _assess_business_impact(finding: Finding) -> BusinessImpact:
    """Assess business impact based on finding category and severity."""
    category = finding.category.lower()
    severity = finding.severity

    # Security findings
    if category in ["csp", "hsts", "headers", "x-content-type-options", "x-frame-options"]:
        return BusinessImpact(
            user_impact="all users",
            revenue_impact="indirect (security breach = lost trust)",
            fix_effort="5 minutes",
            priority_score=9 if severity == Severity.CRITICAL else 7,
            reasoning="Security vulnerabilities affect all users and can lead to data breaches.",
        )

    # Form accessibility
    if category in ["forms", "labels"]:
        return BusinessImpact(
            user_impact="users with disabilities (~15% of population)",
            revenue_impact="direct (lost conversions from inaccessible forms)",
            fix_effort="30 minutes",
            priority_score=8,
            reasoning="Inaccessible forms prevent users with disabilities from completing actions.",
        )

    # Performance
    if category in ["lcp", "cls", "tbt", "render-blocking"]:
        return BusinessImpact(
            user_impact="all users (especially mobile)",
            revenue_impact="direct (100ms delay = 1% conversion loss)",
            fix_effort="1-2 hours",
            priority_score=7,
            reasoning="Performance directly affects user retention and conversion rates.",
        )

    # SEO
    if category in ["meta-description", "title", "og-tags"]:
        return BusinessImpact(
            user_impact="potential users (search traffic)",
            revenue_impact="indirect (lower search rankings = fewer visitors)",
            fix_effort="5 minutes",
            priority_score=6,
            reasoning="SEO issues reduce organic traffic and discoverability.",
        )

    # Accessibility
    if category in ["contrast", "focus", "keyboard", "landmarks"]:
        return BusinessImpact(
            user_impact="users with disabilities",
            revenue_impact="indirect (legal compliance, brand reputation)",
            fix_effort="1 hour",
            priority_score=6,
            reasoning="Accessibility issues create legal risk and exclude users.",
        )

    # Default - better heuristics based on category patterns
    if category in ["deprecated-api", "third-party", "error-handling", "anti-pattern", "loading"]:
        return BusinessImpact(
            user_impact="developers (code quality)",
            revenue_impact="indirect (maintainability, future bugs)",
            fix_effort="30 minutes",
            priority_score=4,
            reasoning="Code quality issues affect maintainability but not immediate user experience.",
        )

    if category in ["content", "navigation", "landmarks", "semantic"]:
        return BusinessImpact(
            user_impact="users with disabilities (~15% of population)",
            revenue_impact="indirect (legal compliance, accessibility)",
            fix_effort="1 hour",
            priority_score=6,
            reasoning="Semantic HTML and accessibility affect users with disabilities and SEO.",
        )

    if category in ["info-disclosure", "headers"]:
        return BusinessImpact(
            user_impact="all users",
            revenue_impact="indirect (security posture)",
            fix_effort="5 minutes",
            priority_score=5,
            reasoning="Information disclosure is low risk but indicates security hygiene.",
        )

    if category in ["structured-data", "og-tags", "twitter-card"]:
        return BusinessImpact(
            user_impact="potential users (search/social traffic)",
            revenue_impact="indirect (discoverability, social sharing)",
            fix_effort="15 minutes",
            priority_score=5,
            reasoning="Structured data and social tags improve discoverability.",
        )

    if category in ["performance-anti-pattern", "caching", "compression"]:
        return BusinessImpact(
            user_impact="all users (especially mobile)",
            revenue_impact="direct (page speed affects conversions)",
            fix_effort="1-2 hours",
            priority_score=6,
            reasoning="Performance optimizations improve user experience and conversions.",
        )

    # Truly unknown categories
    return BusinessImpact(
        user_impact="varies by implementation",
        revenue_impact="indirect",
        fix_effort="varies",
        priority_score=4,
        reasoning="Impact depends on specific use case and implementation.",
    )


def _generate_code_fix(finding: Finding) -> Optional[str]:
    """Generate specific code fix based on finding category."""
    category = finding.category.lower()

    if category == "csp":
        return """Add this to your nginx config or hosting panel:
```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none';" always;
```"""

    if category == "hsts":
        return """Add this to your nginx config:
```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
```"""

    if category in ["x-content-type-options", "x-frame-options", "referrer-policy", "permissions-policy"]:
        header = {
            "x-content-type-options": ("X-Content-Type-Options", "nosniff"),
            "x-frame-options": ("X-Frame-Options", "DENY"),
            "referrer-policy": ("Referrer-Policy", "strict-origin-when-cross-origin"),
            "permissions-policy": ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
        }.get(category, ("X-Content-Type-Options", "nosniff"))
        return f"""Add this to your nginx config:
```nginx
add_header {header[0]} "{header[1]}" always;
```"""

    if category in ["forms", "labels"]:
        return """Add labels to your form inputs:
```html
<!-- Before -->
<input type="text" name="email" placeholder="Email">

<!-- After -->
<label for="email">Email Address</label>
<input type="text" id="email" name="email" placeholder="Enter your email">
```"""

    if category == "meta-description":
        return """Update your meta description (150-160 characters):
```html
<meta name="description" content="Your concise, compelling description here. Include your main value proposition and a call to action.">
```"""

    if category == "title":
        return """Update your title tag (30-60 characters):
```html
<title>Your Product Name — Main Benefit for Users</title>
```"""

    if category in ["contrast", "focus"]:
        return """Add focus styles to your CSS:
```css
/* Ensure all interactive elements have visible focus */
:focus-visible {
  outline: 2px solid #6366f1;
  outline-offset: 2px;
  border-radius: 4px;
}
```"""

    if category == "landmarks":
        return """Add semantic HTML landmarks:
```html
<body>
  <header>...</header>
  <nav aria-label="Main navigation">...</nav>
  <main id="main-content">...</main>
  <footer>...</footer>
</body>
```"""

    if category == "alt-text":
        return """Add alt text to images:
```html
<!-- Informative image -->
<img src="chart.png" alt="Bar chart showing 40% growth in Q4 2025">

<!-- Decorative image -->
<img src="divider.png" alt="" role="presentation">
```"""

    if category == "keyboard":
        return """Ensure keyboard accessibility:
```html
<!-- Add tabindex to interactive elements -->
<div role="button" tabindex="0" onclick="handleClick()" onkeydown="handleKeyDown(event)">
  Click me
</div>

<!-- CSS for focus states -->
<style>
:focus-visible {
  outline: 2px solid #6366f1;
  outline-offset: 2px;
}
</style>
```"""

    if category == "aria":
        return """Add ARIA attributes:
```html
<!-- For buttons without text -->
<button aria-label="Close dialog">
  <svg><!-- X icon --></svg>
</button>

<!-- For expandable content -->
<button aria-expanded="false" aria-controls="content-1">
  Show more
</button>
<div id="content-1" hidden>...</div>

<!-- For live regions -->
<div aria-live="polite" aria-atomic="true">
  Status updates appear here
</div>
```"""

    if category == "structured-data":
        return """Add JSON-LD structured data:
```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebSite",
  "name": "Your Site Name",
  "url": "https://yoursite.com/",
  "description": "Your site description",
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://yoursite.com/search?q={search_term_string}",
    "query-input": "required name=search_term_string"
  }
}
</script>
```"""

    if category in ["deprecated-api", "third-party"]:
        return """Update deprecated API usage:
```javascript
// Instead of deprecated performance.timing
// Use Performance Observer API
const observer = new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    console.log(entry.name, entry.startTime);
  }
});
observer.observe({ entryTypes: ['navigation', 'resource'] });
```"""

    if category in ["error-handling", "anti-pattern"]:
        return """Add error handling:
```javascript
// Add .catch() to promises
navigator.serviceWorker.getRegistrations()
  .then(function(registrations) {
    registrations.forEach(function(reg) { reg.unregister(); });
  })
  .catch(function(error) {
    console.warn('Service worker cleanup failed:', error);
  });
```"""

    if category in ["navigation", "links"]:
        return """Improve navigation accessibility:
```html
<!-- Add skip-to-content link -->
<body>
  <a href="#main-content" class="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:px-4 focus:py-2 focus:bg-white focus:shadow-lg">
    Skip to main content
  </a>
  <!-- ... rest of content ... -->
  <main id="main-content">
    <!-- Main content here -->
  </main>
</body>
```"""

    return None


def _get_fix_group(finding: Finding) -> str:
    """Determine which fix group a finding belongs to."""
    category = finding.category.lower()

    for group_id, rule in DEDUP_GROUPS.items():
        if category in rule["categories"]:
            return group_id

    return category


def _severity_rank(severity: Severity) -> int:
    """Rank severity for sorting."""
    return {
        Severity.CRITICAL: 5,
        Severity.HIGH: 4,
        Severity.MEDIUM: 3,
        Severity.LOW: 2,
        Severity.INFO: 1,
    }.get(severity, 0)


def _estimate_group_effort(findings: list[EnhancedFinding]) -> str:
    """Estimate total fix effort for a group of findings."""
    efforts = [f.business_impact.fix_effort for f in findings if f.business_impact]

    if not efforts:
        return "varies"

    # Simple heuristic: take the longest effort
    if any("day" in e for e in efforts):
        return "1+ days"
    if any("hour" in e for e in efforts):
        return "1-2 hours"
    if any("30 min" in e for e in efforts):
        return "30 minutes"
    return "5-15 minutes"
