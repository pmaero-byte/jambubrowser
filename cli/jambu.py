#!/usr/bin/env python3
"""Jambubrowser CLI — AI-powered webapp auditing from your terminal.

Usage:
    jambu audit <url>          Full audit (6 employees)
    jambu quick <url>          Quick scan (3 employees)
    jambu auth <api-key>       Set API key
    jambu history              Show past audits
    jambu share <audit-id>     Share an audit (generates public link)
    jambu tiers                Show pricing tiers
    jambu health               Check engine status (RAM, CPU, /health checks)
    jambu status               Aggregate system health (engine + supply chain
                               + LLM providers + DB stats + vault)
    jambu diff <mission-id>    Show the diff between the two most recent
                               results of a mission (text delta, sources)

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


def _section(title: str):
    print(f"\n  {title}")
    print(f"  {'─' * max(0, 60 - len(title))}")


def _ok_icon(ok: bool) -> str:
    return "✓" if ok else "✗"


def cmd_status(args):
    """Aggregate system health: engine, supply chain, LLM providers, DB, vault.

    This is the one-shot diagnostic — useful for incident triage, deploy
    verification, or just confirming everything is healthy after a config
    change. Each section is shown even if a previous section failed, so
    you get a complete picture in one command.
    """
    print(f"\n📊 Jambubrowser System Status — {get_engine_url()}")
    print(f"   {'═' * 60}")

    # 1. Engine /health
    health = api_request("GET", "/health")
    print("\n  [1] Engine health")
    if health is None:
        print("    ✗ Engine unreachable")
    else:
        status = health.get("status", "unknown")
        icon = "✓" if status == "ok" else "✗"
        print(f"    {icon} status: {status}")
        ram = health.get("ram_used_gb", 0)
        ram_t = health.get("ram_total_gb", 0)
        if ram_t:
            print(f"    RAM: {ram:.1f} / {ram_t:.1f} GB")
        cpu = health.get("cpu_percent", 0)
        if cpu:
            print(f"    CPU: {cpu:.1f}%")
        for k, v in health.get("checks", {}).items():
            print(f"    {_ok_icon(v == 'ok')} {k}: {v}")

    # 2. Supply chain verification
    sc = api_request("GET", "/security/verify")
    print("\n  [2] Supply chain")
    if sc is None:
        print("    ✗ Cannot reach supply chain verifier")
    else:
        packages = sc.get("packages", {})
        if not packages:
            print("    ⚠ no packages reported")
        else:
            verified = sum(1 for p in packages.values() if p.get("verified"))
            total = len(packages)
            print(f"    {_ok_icon(verified == total)} {verified}/{total} packages verified")
            for name, info in list(packages.items())[:5]:
                icon = _ok_icon(info.get("verified", False))
                ver = info.get("version", "?")
                print(f"      {icon} {name} {ver}")
            if total > 5:
                print(f"      ... and {total - 5} more")

    # 3. LLM providers
    providers = api_request("GET", "/v2/llm/providers")
    print("\n  [3] LLM providers")
    if providers is None:
        print("    ✗ Cannot reach LLM registry")
    elif isinstance(providers, dict):
        items = providers.get("providers", providers) if isinstance(providers.get("providers", None), list) else providers
        if isinstance(items, list):
            for p in items:
                name = p.get("name", "?") if isinstance(p, dict) else str(p)
                healthy = p.get("healthy", True) if isinstance(p, dict) else True
                print(f"    {_ok_icon(healthy)} {name}")
        else:
            print(f"    {items}")

    # 4. DB stats
    stats = api_request("GET", "/stats")
    print("\n  [4] Database")
    if stats is None:
        print("    ✗ Cannot reach /stats")
    elif isinstance(stats, dict):
        for k, v in list(stats.items())[:8]:
            print(f"    • {k}: {v}")

    # 5. Vault
    vault = api_request("GET", "/vault/status")
    print("\n  [5] Vault")
    if vault is None:
        print("    ✗ Cannot reach /vault/status")
    elif isinstance(vault, dict):
        locked = vault.get("locked", True)
        creds = vault.get("credential_count", "?")
        print(f"    {'🔒' if locked else '🔓'} locked={locked}, credentials={creds}")

    print(f"\n   {'═' * 60}\n")


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

    listing = api_request("GET", f"/mission/{args.mission_id}/results", {"limit": 2})
    if not listing:
        return
    results = listing.get("results", [])
    if len(results) < 2:
        print(f"Mission {args.mission_id} has {len(results)} result(s); need at least 2 to diff.")
        return

    a_id = results[1]["id"]  # older
    b_id = results[0]["id"]  # newer
    diff = api_request(
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

    p_status = subparsers.add_parser(
        "status",
        help="Aggregate system health (engine + supply chain + LLM + DB + vault)",
    )

    p_diff = subparsers.add_parser(
        "diff",
        help="Show the diff between the two most recent results of a mission",
    )
    p_diff.add_argument("mission_id", nargs="?", help="Mission ID to diff")

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
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "diff":
        cmd_diff(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
