"""Recurring audit monitors with regression alerting.

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


def cmd_monitor(args) -> int:
    """Manage recurring audit monitors (regression alerts)."""
    sub = getattr(args, "monitor_command", None)
    if sub is None:
        print("Usage: jambu monitor {add,list,rm,run,runs,screenshot,diff} ...")
        print("       jambu monitor add <url> [--interval 60] [--mode quick|full]")
        print("                                [--fail-on high] [--webhook URL] [--run-now]")
        print("       jambu monitor screenshot <monitor-id> <run-id> [--out shot.png]")
        print("       jambu monitor diff <monitor-id> <run-id> [--out diff.png]")
        return core.EXIT_OK

    if sub == "add":
        url = args.url
        if not url.startswith("http"):
            url = "https://" + url
        payload = {
            "url": url,
            "mode": args.mode,
            "interval_minutes": args.interval,
            "fail_on": args.fail_on,
            "run_now": args.run_now,
            "visual_threshold_pct": args.visual_threshold,
        }
        if args.webhook:
            payload["webhook_url"] = args.webhook
        resp = core.api_request("POST", "/audit/monitors", payload)
        if not resp:
            return core.EXIT_ENGINE_ERROR
        m = resp["monitor"]
        print(f"\n✓ Monitor #{m['id']} created")
        print(f"   URL: {m['url']}")
        print(f"   Mode: {m['mode']} · every {m['interval_minutes']} min · "
              f"alert on {m['fail_on']}+ findings")
        if m.get("visual_threshold_pct"):
            print(f"   Visual alerts: >{m['visual_threshold_pct']}% pixel change")
        if m.get("webhook_url"):
            print(f"   Webhook: {m['webhook_url']}")
        run = resp.get("initial_run")
        if run:
            print(f"\n   Baseline run: {core._run_summary_line(run)}")
            if run.get("alert_findings"):
                for f in run["alert_findings"]:
                    print(f"      [{f.get('severity', '?').upper()}] {f.get('title', '?')}")
        return core.EXIT_OK

    if sub == "list":
        resp = core.api_request("GET", "/audit/monitors")
        if not resp:
            return core.EXIT_ENGINE_ERROR
        monitors = resp.get("monitors", [])
        if not monitors:
            print("No monitors yet. Create one: jambu monitor add https://example.com")
            return core.EXIT_OK
        print(f"\n🛰  Audit Monitors ({len(monitors)})\n")
        print(f"{'ID':>3}  {'State':>5}  {'Every':>7}  {'Alert':>8}  {'Last':<16}  {'Findings':>8}  URL")
        print(f"{'─' * 100}")
        for m in monitors:
            state = "on" if m.get("enabled") else "off"
            last = core._fmt_ts(m.get("last_run_at"))
            findings = m.get("last_finding_count")
            findings_str = "-" if findings is None else str(findings)
            print(f"{m['id']:>3}  {state:>5}  {m['interval_minutes']:>5}m  "
                  f"{m['fail_on']:>8}  {last:<16}  {findings_str:>8}  {m['url']}")
        return core.EXIT_OK

    if sub == "rm":
        resp = core.api_request("DELETE", f"/audit/monitors/{args.monitor_id}")
        if not resp:
            return core.EXIT_ENGINE_ERROR
        print(f"✓ Monitor #{args.monitor_id} deleted")
        return core.EXIT_OK

    if sub == "run":
        resp = core.api_request("POST", f"/audit/monitors/{args.monitor_id}/run")
        if not resp:
            return core.EXIT_ENGINE_ERROR
        if resp.get("status") == "error":
            print(f"❌ Monitor #{args.monitor_id} run failed: {resp.get('error')}")
            return core.EXIT_ENGINE_ERROR
        print(f"\n🛰  Monitor #{args.monitor_id} — {resp.get('url')}")
        print(f"   {core._run_summary_line(resp)}")
        if resp.get("alert_findings"):
            print("   New findings requiring attention:")
            for f in resp["alert_findings"]:
                print(f"      [{f.get('severity', '?').upper()}] {f.get('title', '?')}")
        return core.EXIT_OK

    if sub == "runs":
        resp = core.api_request("GET",
                           f"/audit/monitors/{args.monitor_id}/runs?limit={args.limit}")
        if not resp:
            return core.EXIT_ENGINE_ERROR
        runs = resp.get("runs", [])
        if not runs:
            print(f"Monitor #{args.monitor_id} has no runs yet "
                  f"(try: jambu monitor run {args.monitor_id})")
            return core.EXIT_OK
        print(f"\n🛰  Monitor #{args.monitor_id} — {len(runs)} run(s)\n")
        for r in runs:
            print(f"   {core._fmt_ts(r.get('run_at'))}  {core._run_summary_line(r)}")
        return core.EXIT_OK

    if sub == "screenshot":
        data = core.api_request_bytes(
            f"/audit/monitors/{args.monitor_id}/runs/{args.run_id}/screenshot"
        )
        if not data:
            return core.EXIT_ENGINE_ERROR
        out = args.out or f"monitor-{args.monitor_id}-run-{args.run_id}.png"
        path = Path(out)
        if str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"✓ Screenshot written to {path} ({len(data)} bytes)")
        return core.EXIT_OK

    if sub == "diff":
        data = core.api_request_bytes(
            f"/audit/monitors/{args.monitor_id}/runs/{args.run_id}/diff"
        )
        if not data:
            return core.EXIT_ENGINE_ERROR
        out = args.out or f"monitor-{args.monitor_id}-run-{args.run_id}-diff.png"
        path = Path(out)
        if str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"✓ Diff image written to {path} ({len(data)} bytes)")
        print("  Red pixels changed since the previous run; the rest is dimmed.")
        return core.EXIT_OK

    print(f"Unknown monitor subcommand: {sub}")
    return core.EXIT_OK


def register(subparsers) -> None:
    """Declare the `monitor` command and its sub-subcommands."""
    p_monitor = subparsers.add_parser(
        "monitor",
        help="Recurring audit monitors with regression alerts",
    )
    m_sub = p_monitor.add_subparsers(dest="monitor_command")

    p_m_add = m_sub.add_parser("add", help="Create a monitor")
    p_m_add.add_argument("url", help="URL to watch")
    p_m_add.add_argument("--interval", type=int, default=1440,
                         help="Minutes between runs (min 5, default 1440 = daily)")
    p_m_add.add_argument("--mode", choices=["quick", "full"], default="quick")
    p_m_add.add_argument("--fail-on", dest="fail_on",
                         choices=["critical", "high", "medium", "low", "none"],
                         default="high",
                         help="Alert on new findings at or above this severity")
    p_m_add.add_argument("--webhook", help="POST regression alerts to this URL")
    p_m_add.add_argument("--visual-threshold", dest="visual_threshold", type=float,
                         default=2.0,
                         help="Alert when this percent of pixels change (0 disables visual alerts)")
    p_m_add.add_argument("--run-now", dest="run_now", action="store_true",
                         help="Run the baseline audit immediately")

    m_sub.add_parser("list", help="List monitors")

    p_m_rm = m_sub.add_parser("rm", help="Delete a monitor")
    p_m_rm.add_argument("monitor_id", type=int)

    p_m_run = m_sub.add_parser("run", help="Run a monitor now")
    p_m_run.add_argument("monitor_id", type=int)

    p_m_runs = m_sub.add_parser("runs", help="Show a monitor's run history")
    p_m_runs.add_argument("monitor_id", type=int)
    p_m_runs.add_argument("--limit", type=int, default=10)

    p_m_shot = m_sub.add_parser(
        "screenshot", help="Download a run's screenshot PNG",
    )
    p_m_shot.add_argument("monitor_id", type=int)
    p_m_shot.add_argument("run_id", type=int)
    p_m_shot.add_argument(
        "--out", default=None,
        help="Output file (default monitor-<id>-run-<rid>.png)",
    )

    p_m_diff = m_sub.add_parser(
        "diff", help="Download a run's visual-diff heatmap PNG",
    )
    p_m_diff.add_argument("monitor_id", type=int)
    p_m_diff.add_argument("run_id", type=int)
    p_m_diff.add_argument(
        "--out", default=None,
        help="Output file (default monitor-<id>-run-<rid>-diff.png)",
    )
