#!/usr/bin/env python3
"""Jambubrowser CLI — AI-powered webapp auditing from your terminal.

Usage:
    jambu audit <url>          Full audit (6 employees)
    jambu quick <url>          Quick scan (3 employees)
    jambu auth <api-key>       Set API key
    jambu history              Show past audits
    jambu share <audit-id>     Share an audit (generates public link)
    jambu tiers                Show pricing tiers
    jambu health               Check engine status

Set JAMBU_ENGINE_URL to override the default (http://127.0.0.1:8001).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

CONFIG_DIR = Path.home() / ".jambu"
CONFIG_FILE = CONFIG_DIR / "config.json"

SEVERITY_ICONS = {
    "critical": "\033[91m●\033[0m",
    "high": "\033[93m●\033[0m",
    "medium": "\033[33m●\033[0m",
    "low": "\033[94m●\033[0m",
    "info": "\033[90m●\033[0m",
}

SEVERITY_LABELS = {
    "critical": "\033[91mCRITICAL\033[0m",
    "high": "\033[93m   HIGH\033[0m",
    "medium": "\033[33m MEDIUM\033[0m",
    "low": "\033[94m    LOW\033[0m",
    "info": "\033[90m   INFO\033[0m",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def get_api_key() -> str | None:
    config = load_config()
    return config.get("api_key")


def get_engine_url() -> str:
    return os.environ.get("JAMBU_ENGINE_URL", "http://127.0.0.1:8001")


def api_request(method: str, path: str, data: dict = None, stream: bool = False) -> dict | None:
    url = get_engine_url() + path
    headers = {"Content-Type": "application/json"}
    api_key = get_api_key()
    if api_key:
        headers["X-API-Key"] = api_key

    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)

    try:
        resp = urlopen(req, timeout=180)
        if stream:
            return resp
        return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get("detail", body)
        except:
            detail = body
        print(f"\033[91mError: {e.code} — {detail}\033[0m")
        return None
    except URLError as e:
        print(f"\033[91mError: Cannot reach engine at {get_engine_url()}\033[0m")
        print(f"Start the engine: python3 -m uvicorn backend.engine:app --port 8001")
        return None


def cmd_auth(args):
    if not args.api_key:
        print("Usage: jambu auth <api-key>")
        print("Get a key at: https://jambubrowser.com/api-keys/create")
        return

    config = load_config()
    config["api_key"] = args.api_key
    save_config(config)
    print(f"✓ API key saved to {CONFIG_FILE}")


def cmd_audit(args, mode: str = "full"):
    url = args.url
    if not url.startswith("http"):
        url = "https://" + url

    print(f"\n🔍 Jambubrowser {'Quick Scan' if mode == 'quick' else 'Full Audit'}")
    print(f"   URL: {url}")
    print(f"   Engine: {get_engine_url()}")
    print()

    resp = api_request("POST", "/audit/quick" if mode == "quick" else "/audit/run",
                       {"url": url, "mode": mode}, stream=True)
    if not resp:
        return

    findings = []
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
                        icon = SEVERITY_ICONS.get(sev, "?")
                        label = SEVERITY_LABELS.get(sev, "?")
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
                    by_sev = data.get("by_severity", {})
                    print(f"\n{'─' * 60}")
                    print(f"   📋 TOTAL: {total} findings")
                    sev_parts = []
                    for s in ["critical", "high", "medium", "low", "info"]:
                        cnt = by_sev.get(s, 0)
                        if cnt > 0:
                            icon = SEVERITY_ICONS.get(s, "?")
                            sev_parts.append(f"{icon} {s}: {cnt}")
                    print(f"   {' | '.join(sev_parts)}")
                    print(f"{'─' * 60}")

    except KeyboardInterrupt:
        print("\n\n   ⚠ Cancelled by user")

    if findings and mode == "full":
        print(f"\n💡 Tip: jambu share <id> to generate a shareable link")


def cmd_quick(args):
    cmd_audit(args, mode="quick")


def cmd_history(args):
    resp = api_request("GET", "/audit/history")
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

    resp = api_request("POST", f"/audit/history/{args.audit_id}/share")
    if not resp:
        return

    token = resp.get("share_token", "")
    url = get_engine_url() + resp.get("share_url", "")
    print(f"\n🔗 Share link generated!")
    print(f"   Token: {token}")
    print(f"   URL: {url}")
    print(f"\n   Anyone with this link can view the audit results.")


def cmd_tiers(args):
    resp = api_request("GET", "/billing/tiers")
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


def cmd_health(args):
    resp = api_request("GET", "/health")
    if not resp:
        return

    print(f"\n✓ Engine is {resp.get('status', 'unknown')}")
    print(f"  RAM: {resp.get('ram_used_gb', 0):.1f} / {resp.get('ram_total_gb', 0):.1f} GB")
    print(f"  CPU: {resp.get('cpu_percent', 0):.1f}%")
    checks = resp.get("checks", {})
    for k, v in checks.items():
        icon = "✓" if v == "ok" else "✗"
        print(f"  {icon} {k}: {v}")


def main():
    parser = argparse.ArgumentParser(
        prog="jambu",
        description="Jambubrowser CLI — AI-powered webapp auditing",
    )
    subparsers = parser.add_subparsers(dest="command")

    p_auth = subparsers.add_parser("auth", help="Set API key")
    p_auth.add_argument("api_key", nargs="?", help="Your API key (jambu_...)")

    p_audit = subparsers.add_parser("audit", help="Full audit (6 employees)")
    p_audit.add_argument("url", help="URL to audit")

    p_quick = subparsers.add_parser("quick", help="Quick scan (3 employees)")
    p_quick.add_argument("url", help="URL to scan")

    p_history = subparsers.add_parser("history", help="Show past audits")

    p_share = subparsers.add_parser("share", help="Share an audit")
    p_share.add_argument("audit_id", nargs="?", type=int, help="Audit ID to share")

    p_tiers = subparsers.add_parser("tiers", help="Show pricing tiers")

    p_health = subparsers.add_parser("health", help="Check engine status")

    args = parser.parse_args()

    if args.command == "auth":
        cmd_auth(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "quick":
        cmd_quick(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "share":
        cmd_share(args)
    elif args.command == "tiers":
        cmd_tiers(args)
    elif args.command == "health":
        cmd_health(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
