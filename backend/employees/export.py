"""
Export utilities for AI Employee findings.

Produces:
- SARIF 2.1.0 (industry-standard static-analysis format, accepted by
  GitHub Code Scanning, GitLab Code Quality, Azure DevOps, VS Code SARIF
  Viewer, and most modern security/CI tooling).
- Canonical JSON (lossless, machine-readable, suitable for custom dashboards
  and downstream automation).
- Markdown (human-readable, mirrors the AuditPanel export).

All three are pure functions — no I/O — so they're trivial to test and safe
to call from any endpoint or background task.

The SARIF output follows the OASIS SARIF 2.1.0 specification:
https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from html import escape
from typing import Any, Iterable, Mapping, Sequence

from .base import Finding


# ---------------------------------------------------------------------------
# Severity → SARIF level mapping
# ---------------------------------------------------------------------------
# SARIF v2.1.0 defines `level` as one of: "none", "note", "warning", "error".
# We map the Jambubrowser five-bucket severity onto those four buckets.
_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "none",
}


# Stable ordering used by the markdown export.
_SEV_ORDER = ("critical", "high", "medium", "low", "info")
_SEV_RANK = {s: i for i, s in enumerate(_SEV_ORDER)}


def _sev(f: Finding) -> str:
    s = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
    return str(s).lower()


# ---------------------------------------------------------------------------
# Employee → SARIF rule taxonomy
# ---------------------------------------------------------------------------
def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _rule_id(employee: str, category: str) -> str:
    slug = _slug(employee) or "unknown"
    cat = _slug(category) or "uncategorised"
    return f"jambu/{slug}/{cat}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _finding_to_sarif_result(finding: Finding, index: int) -> dict[str, Any]:
    """Convert one Finding to a SARIF result object."""
    severity_value = _sev(finding)
    level = _SARIF_LEVEL.get(severity_value, "warning")
    rule_id = _rule_id(finding.employee, finding.category)
    message_text = (finding.description or finding.title or "No description provided.").strip()

    result: dict[str, Any] = {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": message_text},
        "properties": {
            "jambu": {
                "id": finding.id,
                "employee": finding.employee,
                "severity": severity_value,
                "category": finding.category,
                "title": finding.title,
                "fix_suggestion": finding.fix_suggestion,
                "evidence_snippet": finding.evidence_snippet,
                "wcag_criterion": finding.wcag_criterion,
                "score_impact": finding.score_impact,
            }
        },
    }

    location: dict[str, Any] = {
        "physicalLocation": {
            "artifactLocation": {
                "uri": finding.evidence_snippet or "page",
                "uriBaseId": "AUDITED_URL",
            }
        }
    }
    if finding.evidence_snippet:
        snippet = finding.evidence_snippet
        if len(snippet) > 4000:
            snippet = snippet[:4000] + "…"
        location["physicalLocation"]["contextRegion"] = {
            "snippet": {"text": snippet},
            "startLine": 1,
        }
    result["locations"] = [location]

    if finding.fix_suggestion:
        fix: dict[str, Any] = {"description": {"text": finding.fix_suggestion}}
        if "\n" in finding.fix_suggestion:
            fix["artifactChanges"] = [
                {
                    "artifactLocation": {"uri": "AUDITED_URL"},
                    "replacements": [
                        {
                            "deletedRegion": {"startLine": 1},
                            "insertedContent": {"text": finding.fix_suggestion},
                        }
                    ],
                }
            ]
        result["fixes"] = [fix]

    return result


def content_fingerprint(finding: Finding | Mapping[str, Any]) -> str:
    """Stable identity of a finding across re-audits.

    Built from the fields that survive a re-run (employee + category +
    severity + title) rather than the per-audit UUID, so dismissals and
    external trackers keep matching after a re-audit. Accepts either a
    ``Finding`` or its ``to_dict()`` form.
    """
    if isinstance(finding, Finding):
        data = finding.to_dict()
    else:
        data = dict(finding)
    payload = "|".join(
        [
            str(data.get("employee", "")),
            str(data.get("category", "")),
            str(data.get("severity", "")),
            str(data.get("title", "")),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _finding_to_canonical_dict(finding: Finding) -> dict[str, Any]:
    """Lossless representation of a Finding for JSON pipelines."""
    data = finding.to_dict()
    data["content_hash"] = content_fingerprint(finding)
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def findings_to_sarif(
    findings: Iterable[Finding],
    *,
    audited_url: str,
    tool_name: str = "Jambubrowser AI Employees",
    tool_version: str = "1.0.0",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Convert an iterable of Finding objects to a SARIF 2.1.0 log.

    The output is a single `runs[0]` invocation of the Jambubrowser tool.
    `audited_url` is added as an `originalUriBaseIds` so consumers can resolve
    relative locations against the audited page.
    """
    results: list[dict[str, Any]] = []
    rules: dict[str, dict[str, Any]] = {}
    for i, f in enumerate(findings):
        results.append(_finding_to_sarif_result(f, i))
        rid = _rule_id(f.employee, f.category)
        if rid not in rules:
            rules[rid] = {
                "id": rid,
                "name": _slug(f.category) or "uncategorised",
                "shortDescription": {"text": f.title or f.category or rid},
                "fullDescription": {
                    "text": f.description or f.title or "Jambubrowser finding."
                },
                "defaultConfiguration": {
                    "level": _SARIF_LEVEL.get(_sev(f), "warning")
                },
                "properties": {
                    "jambu": {"employee": f.employee, "category": f.category}
                },
            }

    return {
        "$schema": (
            "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0-rtm.5.json"
        ),
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": tool_version,
                        "informationUri": "https://jambubrowser.local/audit",
                        "rules": list(rules.values()),
                    }
                },
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "properties": {
                            "jambu": {
                                "audited_url": audited_url,
                                "run_id": run_id or uuid.uuid4().hex,
                                "finding_count": len(results),
                            }
                        },
                    }
                ],
                "originalUriBaseIds": {"AUDITED_URL": {"uri": audited_url}},
                "results": results,
            }
        ],
    }


def findings_to_canonical_json(
    findings: Iterable[Finding],
    *,
    audited_url: str,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Lossless JSON of all findings + a small summary envelope."""
    items = [_finding_to_canonical_dict(f) for f in findings]
    by_severity: dict[str, int] = {}
    by_employee: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for item in items:
        sev = str(item.get("severity", "")).lower()
        emp = str(item.get("employee", "unknown"))
        cat = str(item.get("category", "uncategorised")) or "uncategorised"
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_employee[emp] = by_employee.get(emp, 0) + 1
        by_category[cat] = by_category.get(cat, 0) + 1

    return {
        "audited_url": audited_url,
        "generated_at": _utc_now_iso(),
        "total_findings": len(items),
        "by_severity": by_severity,
        "by_employee": by_employee,
        "by_category": by_category,
        "summary": dict(summary) if summary else {},
        "findings": items,
    }


def sarif_to_json(sarif: Mapping[str, Any]) -> str:
    """Pretty-print SARIF. JSON is the only valid wire format for SARIF."""
    return json.dumps(sarif, indent=2, sort_keys=False, default=str)


def findings_to_markdown(
    findings: Sequence[Finding],
    *,
    audited_url: str,
    summary: Mapping[str, Any] | None = None,
) -> str:
    """Human-readable Markdown export. Mirrors AuditPanel's existing format."""
    by_sev: dict[str, list[Finding]] = {}
    for f in findings:
        by_sev.setdefault(_sev(f), []).append(f)

    lines: list[str] = [
        f"# Audit Report — {audited_url}",
        "",
        f"**Generated:** {_utc_now_iso()}  ",
        f"**Total findings:** {len(findings)}  ",
    ]
    if summary:
        for k, v in summary.items():
            lines.append(f"**{k}:** {v}  ")
    lines.append("")

    if by_sev:
        lines.append("## Summary by Severity")
        lines.append("")
        for sev in _SEV_ORDER:
            n = len(by_sev.get(sev, []))
            if n:
                lines.append(f"- **{sev.title()}**: {n}")
        lines.append("")

    by_emp: dict[str, list[Finding]] = {}
    for f in findings:
        by_emp.setdefault(f.employee or "Unknown", []).append(f)

    for emp, items in sorted(by_emp.items()):
        lines.append(f"## {emp} ({len(items)} findings)")
        lines.append("")
        for f in sorted(items, key=lambda x: (_SEV_RANK.get(_sev(x), 99), x.title)):
            sev_label = _sev(f).title()
            lines.append(f"### {sev_label}: {f.title}")
            lines.append("")
            lines.append(f"- **Category:** `{f.category}`")
            lines.append(f"- **Description:** {f.description}")
            if f.evidence_snippet:
                lines.append(f"- **Evidence:** `{f.evidence_snippet}`")
            if f.fix_suggestion:
                lines.append(f"- **Suggested fix:** {f.fix_suggestion}")
            if f.wcag_criterion:
                lines.append(f"- **WCAG:** {f.wcag_criterion}")
            if f.score_impact:
                lines.append(f"- **Score impact:** {f.score_impact}")
            lines.append("")

    return "\n".join(lines)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
# Self-contained (inline CSS, no external assets) and escaped: finding text
# is LLM-authored prose about arbitrary pages, so anything interpolated into
# the page MUST go through html.escape. The report is print-friendly
# (browser → Save as PDF) and used for both saved audits and share links.

_HTML_SEV_COLOR = {
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#d97706",
    "low": "#2563eb",
    "info": "#6b7280",
}


def _html_sev(f: Finding) -> str:
    return _sev(f) if _sev(f) in _HTML_SEV_COLOR else "info"


def _html_finding_card(f: Finding) -> str:
    sev = _html_sev(f)
    color = _HTML_SEV_COLOR[sev]
    parts: list[str] = []
    parts.append(f'<article class="finding" style="--sev:{color}">')
    parts.append(
        '<div class="finding-head">'
        f'<span class="sev">{escape(sev.title())}</span>'
        f'<h3>{escape(f.title or "Untitled finding")}</h3>'
        "</div>"
    )
    meta = [f"<code>{escape(f.employee or 'Unknown')}</code>"]
    if f.category:
        meta.append(f"<code>{escape(f.category)}</code>")
    if f.wcag_criterion:
        meta.append(f"WCAG <code>{escape(str(f.wcag_criterion))}</code>")
    if f.score_impact:
        meta.append(escape(str(f.score_impact)))
    parts.append(f'<p class="meta">{" · ".join(meta)}</p>')

    if f.description:
        parts.append(f"<p>{escape(f.description)}</p>")
    if f.fix_suggestion:
        parts.append(
            f'<div class="fix"><strong>Suggested fix:</strong> {escape(f.fix_suggestion)}</div>'
        )
    if f.evidence_snippet:
        snippet = f.evidence_snippet[:2000]
        if len(f.evidence_snippet) > 2000:
            snippet += "…"
        parts.append(f"<pre>{escape(snippet)}</pre>")
    parts.append("</article>")
    return "".join(parts)


def findings_to_html(
    findings: Sequence[Finding],
    *,
    audited_url: str,
    summary: Mapping[str, Any] | None = None,
    title: str = "Jambubrowser Audit Report",
) -> str:
    """Render findings as a self-contained, escaped, print-friendly HTML page."""
    by_sev: dict[str, list[Finding]] = {}
    for f in findings:
        by_sev.setdefault(_html_sev(f), []).append(f)

    counts = {s: len(by_sev.get(s, [])) for s in _SEV_ORDER}
    cards: list[str] = []
    for sev in _SEV_ORDER:
        items = sorted(by_sev.get(sev, []), key=lambda x: (x.employee, x.title))
        if not items:
            continue
        cards.append(f'<h2 class="sev-head" style="--sev:{_HTML_SEV_COLOR[sev]}">'
                     f'{sev.title()} <span>{len(items)}</span></h2>')
        cards.extend(_html_finding_card(f) for f in items)

    summary_rows = ""
    if summary:
        rows = "".join(
            f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>"
            for k, v in summary.items()
            if v is not None
        )
        if rows:
            summary_rows = f'<table class="summary-meta"><tbody>{rows}</tbody></table>'

    stats = "".join(
        f'<div class="stat" style="--sev:{_HTML_SEV_COLOR[s]}">'
        f'<span class="stat-n">{counts[s]}</span><span class="stat-l">{s}</span></div>'
        for s in _SEV_ORDER
    )

    body = "".join(cards) if cards else (
        '<p class="empty">No active findings — this page passed every check.</p>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — {escape(audited_url)}</title>
<style>
  :root {{ --fg:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f9fafb; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:32px 20px 64px; background:var(--bg); color:var(--fg);
         font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  main {{ max-width: 860px; margin: 0 auto; }}
  header.report {{ border-bottom: 2px solid var(--line); padding-bottom: 16px; margin-bottom: 24px; }}
  header.report h1 {{ margin: 0 0 6px; font-size: 22px; }}
  .url {{ color: var(--muted); word-break: break-all; }}
  .meta-line {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  .stats {{ display:flex; gap:10px; flex-wrap:wrap; margin:20px 0 8px; }}
  .stat {{ background:#fff; border:1px solid var(--line); border-left:4px solid var(--sev,var(--muted));
           border-radius:8px; padding:10px 14px; min-width:86px; }}
  .stat-n {{ display:block; font-size:22px; font-weight:700; }}
  .stat-l {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }}
  .summary-meta {{ border-collapse: collapse; margin: 12px 0 0; font-size: 13px; }}
  .summary-meta th {{ text-align:left; color:var(--muted); font-weight:600; padding:2px 12px 2px 0; }}
  .summary-meta td {{ padding:2px 0; }}
  h2.sev-head {{ font-size:15px; text-transform:uppercase; letter-spacing:.08em;
                 color:var(--sev); margin:32px 0 10px; }}
  h2.sev-head span {{ color:var(--muted); font-weight:400; }}
  .finding {{ background:#fff; border:1px solid var(--line); border-left:4px solid var(--sev);
              border-radius:8px; padding:14px 16px; margin:10px 0; page-break-inside: avoid; }}
  .finding-head {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
  .finding-head h3 {{ margin:0; font-size:16px; }}
  .sev {{ font-size:10px; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
          color:#fff; background:var(--sev); border-radius:4px; padding:2px 6px; }}
  .meta {{ color:var(--muted); font-size:12px; margin:6px 0 8px; }}
  code {{ background:var(--bg); border:1px solid var(--line); border-radius:4px;
          padding:1px 5px; font-size:12px; }}
  .fix {{ background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px;
          padding:8px 10px; margin:8px 0; font-size:14px; }}
  pre {{ background:#0f172a; color:#e2e8f0; border-radius:6px; padding:10px 12px;
         overflow-x:auto; font-size:12px; }}
  .empty {{ color:var(--muted); background:#fff; border:1px dashed var(--line);
            border-radius:8px; padding:24px; text-align:center; }}
  footer {{ margin-top:40px; color:var(--muted); font-size:12px; border-top:1px solid var(--line);
            padding-top:12px; }}
  @media print {{
    body {{ background:#fff; padding:0; }}
    .finding, .stat {{ box-shadow:none; }}
    pre {{ background:#f3f4f6; color:#111827; }}
  }}
</style>
</head>
<body>
<main>
  <header class="report">
    <h1>{escape(title)}</h1>
    <div class="url">{escape(audited_url)}</div>
    <div class="meta-line">Generated {escape(_utc_now_iso())}</div>
    {summary_rows}
  </header>
  <section class="stats">{stats}</section>
  {body}
  <footer>Generated by Jambubrowser AI Employees — security · performance · accessibility · SEO · UX · code quality</footer>
</main>
</body>
</html>"""
