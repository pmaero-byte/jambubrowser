"""Audit, quick scan, history, share, report, tiers and diff.

Handlers call the shared transport and formatters as `core.<name>`, so a
test patches one object (`cli.jambu_cli.core`) no matter which command it
is exercising.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

from cli.jambu_cli import core


def _export_findings(
    args, url: str, done_findings: list | None, fallback_findings: list,
    by_severity: dict, dismissed_count: int, mode: str,
) -> int:
    """Write SARIF / canonical JSON / Markdown exports when requested.

    Uses the engine's post-dedup, post-dismissal findings from the `done`
    event when available (older engines only send per-employee lists, so
    fall back to those). Returns an exit code (core.EXIT_OK or core.EXIT_ENGINE_ERROR).
    """
    if not (args.sarif or args.json_out or args.markdown or args.jira or args.linear):
        return core.EXIT_OK

    try:
        from backend.employees.base import Finding
        from backend.employees.export import (
            findings_to_canonical_json,
            findings_to_jira_issues,
            findings_to_linear_issues,
            findings_to_markdown,
            findings_to_sarif,
            sarif_to_json,
        )
    except ImportError as e:  # pragma: no cover — packaging failure
        print(f"\033[91mError: export modules unavailable ({e})\033[0m")
        return core.EXIT_ENGINE_ERROR

    raw = done_findings if done_findings is not None else fallback_findings
    findings = [Finding.from_dict(f) for f in raw]
    summary = {
        "mode": mode,
        "total_findings": len(findings),
        "by_severity": by_severity,
        "dismissed_count": dismissed_count,
        "engine": core.get_engine_url(),
    }

    if args.sarif:
        sarif = findings_to_sarif(
            findings, audited_url=url, run_id=f"jambu-{int(time.time())}",
        )
        core._write_export(args.sarif, sarif_to_json(sarif), "SARIF")
    if args.json_out:
        body = findings_to_canonical_json(findings, audited_url=url, summary=summary)
        core._write_export(args.json_out, json.dumps(body, indent=2, default=str), "JSON")
    if args.markdown:
        md = findings_to_markdown(findings, audited_url=url, summary=summary)
        core._write_export(args.markdown, md, "Markdown")
    if getattr(args, "jira", None):
        issues = findings_to_jira_issues(findings, audited_url=url)
        core._write_export(args.jira, json.dumps(issues, indent=2, default=str), "Jira")
    if getattr(args, "linear", None):
        issues = findings_to_linear_issues(findings, audited_url=url)
        core._write_export(args.linear, json.dumps(issues, indent=2, default=str), "Linear")
    return core.EXIT_OK


def cmd_audit(args, mode: str = "full") -> int:
    url = args.url
    if not url.startswith("http"):
        url = "https://" + url

    print(f"\n🔍 Jambubrowser {'Quick Scan' if mode == 'quick' else 'Full Audit'}")
    print(f"   URL: {url}")
    print(f"   Engine: {core.get_engine_url()}")
    print()

    resp = core.api_request("POST", "/audit/quick" if mode == "quick" else "/audit/run",
                       {"url": url, "mode": mode}, stream=True)
    if not resp:
        return core.EXIT_ENGINE_ERROR

    findings = []
    done_findings = None
    by_severity: dict = {}
    dismissed_count = 0
    try:
        buffer = ""
        for chunk in iter(lambda: resp.read(4096), b""):
            buffer += chunk.decode()
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                lines = block.strip().split("\n")
                event_type = ""
                data_str = ""
                for line in lines:
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data_str = line[6:]

                if not event_type or not data_str:
                    continue

                try:
                    data = json.loads(data_str)
                except:
                    continue

                if event_type == "status":
                    phase = data.get("phase", "")
                    if phase == "collecting":
                        print("   ⏳ Collecting page data...")
                    elif phase == "collected":
                        print(f"   ✓ Page loaded ({data.get('load_ms', 0):.0f}ms, {data.get('requests', 0)} requests)")
                    elif phase == "analyzing":
                        employees = data.get("employees", [])
                        print(f"   🤖 Dispatching {len(employees)} employees: {', '.join(employees)}")

                elif event_type == "employee_done":
                    name = data.get("employee", "?")
                    count = data.get("findings_count", 0)
                    ms = data.get("elapsed_ms", 0)
                    emoji = data.get("emoji", "🤖")
                    print(f"\n   {emoji} {name} — {count} findings ({ms}ms)")
                    for f in data.get("findings", []):
                        sev = f.get("severity", "?")
                        icon = core.SEVERITY_ICONS.get(sev, "?")
                        label = core.SEVERITY_LABELS.get(sev, "?")
                        cat = f.get("category", "?")
                        title = f.get("title", "?")
                        print(f"      {icon} [{label}] {title}")
                    findings.extend(data.get("findings", []))

                elif event_type == "employee_error":
                    name = data.get("employee", "?")
                    error = data.get("error", "?")
                    print(f"\n   ❌ {name}: {error[:100]}")

                elif event_type == "done":
                    total = data.get("total_findings", 0)
                    by_severity = data.get("by_severity", {})
                    dismissed_count = data.get("dismissed_count", 0)
                    done_findings = data.get("findings")
                    print(f"\n{'─' * 60}")
                    print(f"   📋 TOTAL: {total} findings")
                    sev_parts = []
                    for s in core.SEVERITY_ORDER:
                        cnt = by_severity.get(s, 0)
                        if cnt > 0:
                            icon = core.SEVERITY_ICONS.get(s, "?")
                            sev_parts.append(f"{icon} {s}: {cnt}")
                    print(f"   {' | '.join(sev_parts)}")
                    if dismissed_count:
                        print(f"   🙈 {dismissed_count} dismissed finding(s) hidden")
                    print(f"{'─' * 60}")

                elif event_type == "error":
                    print(f"\n   ❌ Audit failed during {data.get('phase', '?')}: "
                          f"{str(data.get('error', '?'))[:200]}")
                    return core.EXIT_ENGINE_ERROR

    except KeyboardInterrupt:
        print("\n\n   ⚠ Cancelled by user")
        return core.EXIT_ENGINE_ERROR

    code = _export_findings(
        args, url, done_findings, findings, by_severity, dismissed_count, mode,
    )
    if code != core.EXIT_OK:
        return code

    # CI gate: --fail-on <severity> exits 1 when at-or-above findings exist.
    fail_on = getattr(args, "fail_on", "none")
    if fail_on and fail_on != "none":
        threshold = core.SEVERITY_ORDER.index(fail_on)
        failing = sum(by_severity.get(s, 0) for s in core.SEVERITY_ORDER[: threshold + 1])
        if failing:
            print(f"\n❌ Gate failed: {failing} finding(s) at or above '{fail_on}'")
            return core.EXIT_GATE_FAILED
        print(f"\n✅ Gate passed: no findings at or above '{fail_on}'")

    if findings and mode == "full" and not (args.sarif or args.json_out or args.markdown):
        print(f"\n💡 Tip: jambu share <id> to generate a shareable link")
    return core.EXIT_OK


def cmd_quick(args) -> int:
    return cmd_audit(args, mode="quick")


def cmd_history(args):
    resp = core.api_request("GET", "/audit/history")
    if not resp:
        return

    audits = resp.get("audits", [])
    if not audits:
        print("No audit history yet. Run: jambu audit <url>")
        return

    print(f"\n📋 Recent Audits ({len(audits)} total)\n")
    print(f"{'ID':>4}  {'Mode':>6}  {'Findings':>8}  {'URL':<40}  {'Date'}")
    print(f"{'─' * 80}")
    for a in audits:
        audit_id = a.get("id", "?")
        mode = a.get("mode", "?")
        total = a.get("total_findings", 0)
        url = a.get("url", "?")[:40]
        date = a.get("created_at", "?")
        if isinstance(date, float):
            import datetime
            date = datetime.datetime.fromtimestamp(date).strftime("%Y-%m-%d %H:%M")
        print(f"{audit_id:>4}  {mode:>6}  {total:>8}  {url:<40}  {date}")


def cmd_share(args):
    if not args.audit_id:
        print("Usage: jambu share <audit-id>")
        return

    resp = core.api_request("POST", f"/audit/history/{args.audit_id}/share")
    if not resp:
        return

    token = resp.get("share_token", "")
    url = core.get_engine_url() + resp.get("share_url", "")
    print(f"\n🔗 Share link generated!")
    print(f"   Token: {token}")
    print(f"   JSON:  {url}")
    print(f"   Report: {url}/report   (HTML, print-friendly)")
    print(f"\n   Anyone with this link can view the audit results.")


def cmd_report(args) -> int:
    """Download the self-contained HTML report for a saved audit."""
    if not args.audit_id:
        print("Usage: jambu report <audit-id> [--out FILE]")
        return core.EXIT_OK

    html = core.api_request_text(f"/audit/report/{args.audit_id}")
    if html is None:
        return core.EXIT_ENGINE_ERROR

    if args.out == "-":
        print(html)
        return core.EXIT_OK

    out = Path(args.out) if args.out else Path(f"jambu-report-{args.audit_id}.html")
    out.write_text(html, encoding="utf-8")
    print(f"\n📄 Report written to {out}")
    print("   Open it in a browser, or print to PDF (⌘P / Ctrl+P → Save as PDF).")
    return core.EXIT_OK


def cmd_tiers(args):
    resp = core.api_request("GET", "/billing/tiers")
    if not resp:
        return

    tiers = resp.get("tiers", {})
    print(f"\n💎 Jambubrowser Pricing Tiers\n")
    for tier_id, tier in tiers.items():
        name = tier.get("name", tier_id)
        price = tier.get("price_monthly", 0)
        features = tier.get("features", [])
        limits = tier.get("limits", {})

        if isinstance(price, int) and price > 0:
            price_str = f"${price}/month"
        elif price == 0:
            price_str = "Free"
        else:
            price_str = "Custom"

        print(f"  {'─' * 50}")
        print(f"  {name} — {price_str}")
        for f in features:
            print(f"    ✓ {f}")
    print()


def cmd_diff(args):
    """Show the diff between the two most recent results of a mission.

    Fetches the last 2 result rows for *mission_id* from the engine
    and calls /mission/results/compare to compute a structured diff:
    text length delta, word-level similarity, and sources added /
    removed / kept.
    """
    if not args.mission_id:
        print("Usage: jambu diff <mission_id>")
        return

    listing = core.api_request("GET", f"/mission/{args.mission_id}/results?limit=2")
    if not listing:
        return
    results = listing.get("results", [])
    if len(results) < 2:
        print(f"Mission {args.mission_id} has {len(results)} result(s); need at least 2 to diff.")
        return

    a_id = results[1]["id"]  # older
    b_id = results[0]["id"]  # newer
    diff = core.api_request(
        "GET",
        f"/mission/results/compare?result_a={a_id}&result_b={b_id}",
    )
    if not diff:
        return

    text = diff.get("text", {})
    src = diff.get("sources", {})
    status = diff.get("status", {})

    print(f"\n🔍 Mission {args.mission_id} — diff result {a_id} → {b_id}\n")
    print(f"   Text: {text.get('length_a', 0)} → {text.get('length_b', 0)} chars "
          f"(Δ {text.get('length_delta', 0):+d}), {text.get('words_a', 0)} → {text.get('words_b', 0)} words")
    print(f"   Similarity: {text.get('similarity', 0):.0%}  changed: {text.get('changed')}")
    print(f"   Status: {status.get('success_a')} → {status.get('success_b')}  changed: {status.get('changed')}")
    print()
    if src.get("added"):
        print(f"   📥 Sources added ({len(src['added'])}):")
        for s in src["added"][:10]:
            print(f"      + {s}")
        if len(src["added"]) > 10:
            print(f"      ... and {len(src['added']) - 10} more")
    if src.get("removed"):
        print(f"   📤 Sources removed ({len(src['removed'])}):")
        for s in src["removed"][:10]:
            print(f"      - {s}")
        if len(src["removed"]) > 10:
            print(f"      ... and {len(src['removed']) - 10} more")
    if src.get("kept"):
        print(f"   ↔️  Sources kept: {len(src['kept'])}")
    if not (src.get("added") or src.get("removed")):
        print("   (no source changes)")
    print()


def register(subparsers) -> None:
    """Declare the audit surface: full audit, quick scan, and the
    read-only commands over past results."""
    p_audit = subparsers.add_parser(
        "audit",
        help="Full audit (6 employees) with optional SARIF/JSON/Markdown export",
    )
    core._add_audit_options(p_audit)

    p_quick = subparsers.add_parser(
        "quick",
        help="Quick scan (3 employees) with optional SARIF/JSON/Markdown export",
    )
    core._add_audit_options(p_quick)

    p_history = subparsers.add_parser("history", help="Show past audits")

    p_share = subparsers.add_parser("share", help="Share an audit")
    p_share.add_argument("audit_id", nargs="?", type=int, help="Audit ID to share")

    p_report = subparsers.add_parser(
        "report", help="Download the HTML report for a saved audit",
    )
    p_report.add_argument("audit_id", nargs="?", type=int, help="Audit ID")
    p_report.add_argument(
        "--out", default=None,
        help="Output file (default: jambu-report-<id>.html; '-' for stdout)",
    )

    p_tiers = subparsers.add_parser("tiers", help="Show pricing tiers")

    p_diff = subparsers.add_parser(
        "diff",
        help="Show the diff between the two most recent results of a mission",
    )
    p_diff.add_argument("mission_id", nargs="?", help="Mission ID to diff")
