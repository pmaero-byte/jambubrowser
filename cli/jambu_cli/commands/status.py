"""auth, health and the aggregate status view.

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


def cmd_auth(args):
    if not args.api_key:
        print("Usage: jambu auth <api-key>")
        print("Get a key at: https://jambubrowser.com/api-keys/create")
        return

    config = core.load_config()
    config["api_key"] = args.api_key
    core.save_config(config)
    print(f"✓ API key saved to {core.CONFIG_FILE}")


def cmd_health(args):
    resp = core.api_request("GET", "/health")
    if not resp:
        return

    print(f"\n✓ Engine is {resp.get('status', 'unknown')}")
    print(f"  RAM: {resp.get('ram_used_gb', 0):.1f} / {resp.get('ram_total_gb', 0):.1f} GB")
    print(f"  CPU: {resp.get('cpu_percent', 0):.1f}%")
    checks = resp.get("checks", {})
    # "locked" vault and a zero count are healthy states, not failures —
    # only mark real error strings with ✗.
    for k, v in checks.items():
        if isinstance(v, str) and v.startswith("error"):
            icon = "✗"
        elif isinstance(v, int):
            icon = "•"
        else:
            icon = "✓"
        print(f"  {icon} {k}: {v}")


def cmd_status(args):
    """Aggregate system health: engine, supply chain, LLM providers, DB, vault.

    This is the one-shot diagnostic — useful for incident triage, deploy
    verification, or just confirming everything is healthy after a config
    change. Each section is shown even if a previous section failed, so
    you get a complete picture in one command.
    """
    print(f"\n📊 Jambubrowser System Status — {core.get_engine_url()}")
    print(f"   {'═' * 60}")

    # 1. Engine /health
    health = core.api_request("GET", "/health")
    print("\n  [1] Engine health")
    if health is None:
        print("    ✗ Engine unreachable")
    else:
        status = health.get("status", "unknown")
        online_statuses = ("ok", "online", "ready", "healthy")
        print(f"    {core._ok_icon(str(status).lower() in online_statuses)} status: {status}")
        ram = health.get("ram_used_gb", 0)
        ram_t = health.get("ram_total_gb", 0)
        if ram_t:
            print(f"    RAM: {ram:.1f} / {ram_t:.1f} GB")
        cpu = health.get("cpu_percent", 0)
        if cpu:
            print(f"    CPU: {cpu:.1f}%")
        for k, v in health.get("checks", {}).items():
            str_v = str(v).lower()
            failing = str_v in ("error", "missing", "down", "fail", "failed", "unreachable", "locked-error")
            ok = str_v in ("ok", "online", "ready", "healthy", "open", "active")
            if failing:
                icon = "✗"
            elif ok:
                icon = "✓"
            else:
                icon = "•"
            print(f"    {icon} {k}: {v}")

    # 2. Supply chain verification
    sc = core.api_request("GET", "/security/verify")
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
            print(f"    {core._ok_icon(verified == total)} {verified}/{total} packages verified")
            for name, info in list(packages.items())[:5]:
                icon = core._ok_icon(info.get("verified", False))
                ver = info.get("version", "?")
                print(f"      {icon} {name} {ver}")
            if total > 5:
                print(f"      ... and {total - 5} more")

    # 3. LLM providers
    providers = core.api_request("GET", "/v2/llm/providers")
    print("\n  [3] LLM providers")
    if providers is None:
        print("    ✗ Cannot reach LLM registry")
    elif isinstance(providers, dict):
        items = providers.get("providers", providers) if isinstance(providers.get("providers", None), list) else providers
        if isinstance(items, list):
            for p in items:
                name = p.get("name", "?") if isinstance(p, dict) else str(p)
                healthy = p.get("healthy", True) if isinstance(p, dict) else True
                print(f"    {core._ok_icon(healthy)} {name}")
        else:
            print(f"    {items}")

    # 4. DB stats
    stats = core.api_request("GET", "/stats")
    print("\n  [4] Database")
    if stats is None:
        print("    ✗ Cannot reach /stats")
    elif isinstance(stats, dict):
        for k, v in list(stats.items())[:8]:
            print(f"    • {k}: {v}")

    # 5. Vault
    vault = core.api_request("GET", "/vault/status")
    print("\n  [5] Vault")
    if vault is None:
        print("    ✗ Cannot reach /vault/status")
    elif isinstance(vault, dict):
        locked = vault.get("locked", True)
        creds = vault.get("credential_count", "?")
        print(f"    {'🔒' if locked else '🔓'} locked={locked}, credentials={creds}")

    print(f"\n   {'═' * 60}\n")


def register(subparsers) -> None:
    """Declare auth, health and status."""
    p_auth = subparsers.add_parser("auth", help="Set API key")
    p_auth.add_argument("api_key", nargs="?", help="Your API key (jambu_...)")

    p_health = subparsers.add_parser("health", help="Check engine status")

    p_status = subparsers.add_parser(
        "status",
        help="Aggregate system health (engine + supply chain + LLM + DB + vault)",
    )
