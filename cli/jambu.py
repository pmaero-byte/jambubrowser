#!/usr/bin/env python3
"""Jambubrowser CLI — AI-powered webapp auditing from your terminal.

Usage:
    jambu audit <url>          Full audit (6 employees)
    jambu quick <url>          Quick scan (3 employees)
    jambu auth <api-key>       Set API key
    jambu history              Show past audits
    jambu share <audit-id>     Share an audit (generates public link)
    jambu report <audit-id>    Download the HTML report (print-friendly)
    jambu tiers                Show pricing tiers
    jambu health               Check engine status (RAM, CPU, /health checks)
    jambu status               Aggregate system health (engine + supply chain
                               + LLM providers + DB stats + vault)
    jambu diff <mission-id>    Show the diff between the two most recent
                               results of a mission (text delta, sources)
    jambu monitor add <url>    Recurring audit monitor with regression alerts
    jambu monitor list|rm|run|runs|screenshot
                                Manage monitors, inspect runs, download shots
    jambu dcm status           DecentraCode Mesh node overview (peers, models)
    jambu dcm infer <prompt>   Run a prompt on the local DCM mesh

CI usage (exit codes: 0 = pass, 1 = gate failed, 2 = engine error):
    jambu quick https://staging.example.com --sarif out.sarif --fail-on high
    jambu audit https://example.com --json out.json --markdown report.md

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

# Exit codes — CI relies on these, keep them stable.
EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_ENGINE_ERROR = 2

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

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


def api_request_bytes(path: str) -> bytes | None:
    """GET an endpoint that returns binary (e.g. a run screenshot PNG)."""
    url = get_engine_url() + path
    headers = {}
    api_key = get_api_key()
    if api_key:
        headers["X-API-Key"] = api_key
    req = Request(url, headers=headers, method="GET")
    try:
        resp = urlopen(req, timeout=60)
        return resp.read()
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            detail = json.loads(body).get("detail", body)
        except Exception:
            detail = body
        print(f"\033[91mError: {e.code} — {detail}\033[0m")
        return None
    except URLError:
        print(f"\033[91mError: Cannot reach engine at {get_engine_url()}\033[0m")
        return None


def api_request_text(path: str) -> str | None:
    """GET an endpoint that returns non-JSON (e.g. the HTML report)."""
    url = get_engine_url() + path
    headers = {}
    api_key = get_api_key()
    if api_key:
        headers["X-API-Key"] = api_key
    req = Request(url, headers=headers, method="GET")
    try:
        resp = urlopen(req, timeout=60)
        return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            detail = json.loads(body).get("detail", body)
        except Exception:
            detail = body
        print(f"\033[91mError: {e.code} — {detail}\033[0m")
        return None
    except URLError:
        print(f"\033[91mError: Cannot reach engine at {get_engine_url()}\033[0m")
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


def _write_export(path: str, content: str, label: str) -> None:
    """Write an export payload to a file, or stdout when path is '-'."""
    if path == "-":
        print(content)
        return
    out = Path(path)
    if str(out.parent) not in ("", "."):
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"   📄 {label} written to {out}")


def _export_findings(
    args, url: str, done_findings: list | None, fallback_findings: list,
    by_severity: dict, dismissed_count: int, mode: str,
) -> int:
    """Write SARIF / canonical JSON / Markdown exports when requested.

    Uses the engine's post-dedup, post-dismissal findings from the `done`
    event when available (older engines only send per-employee lists, so
    fall back to those). Returns an exit code (EXIT_OK or EXIT_ENGINE_ERROR).
    """
    if not (args.sarif or args.json_out or args.markdown):
        return EXIT_OK

    try:
        from backend.employees.base import Finding
        from backend.employees.export import (
            findings_to_canonical_json,
            findings_to_markdown,
            findings_to_sarif,
            sarif_to_json,
        )
    except ImportError as e:  # pragma: no cover — packaging failure
        print(f"\033[91mError: export modules unavailable ({e})\033[0m")
        return EXIT_ENGINE_ERROR

    raw = done_findings if done_findings is not None else fallback_findings
    findings = [Finding.from_dict(f) for f in raw]
    summary = {
        "mode": mode,
        "total_findings": len(findings),
        "by_severity": by_severity,
        "dismissed_count": dismissed_count,
        "engine": get_engine_url(),
    }

    if args.sarif:
        sarif = findings_to_sarif(
            findings, audited_url=url, run_id=f"jambu-{int(time.time())}",
        )
        _write_export(args.sarif, sarif_to_json(sarif), "SARIF")
    if args.json_out:
        body = findings_to_canonical_json(findings, audited_url=url, summary=summary)
        _write_export(args.json_out, json.dumps(body, indent=2, default=str), "JSON")
    if args.markdown:
        md = findings_to_markdown(findings, audited_url=url, summary=summary)
        _write_export(args.markdown, md, "Markdown")
    return EXIT_OK


def cmd_audit(args, mode: str = "full") -> int:
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
        return EXIT_ENGINE_ERROR

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
                    by_severity = data.get("by_severity", {})
                    dismissed_count = data.get("dismissed_count", 0)
                    done_findings = data.get("findings")
                    print(f"\n{'─' * 60}")
                    print(f"   📋 TOTAL: {total} findings")
                    sev_parts = []
                    for s in SEVERITY_ORDER:
                        cnt = by_severity.get(s, 0)
                        if cnt > 0:
                            icon = SEVERITY_ICONS.get(s, "?")
                            sev_parts.append(f"{icon} {s}: {cnt}")
                    print(f"   {' | '.join(sev_parts)}")
                    if dismissed_count:
                        print(f"   🙈 {dismissed_count} dismissed finding(s) hidden")
                    print(f"{'─' * 60}")

                elif event_type == "error":
                    print(f"\n   ❌ Audit failed during {data.get('phase', '?')}: "
                          f"{str(data.get('error', '?'))[:200]}")
                    return EXIT_ENGINE_ERROR

    except KeyboardInterrupt:
        print("\n\n   ⚠ Cancelled by user")
        return EXIT_ENGINE_ERROR

    code = _export_findings(
        args, url, done_findings, findings, by_severity, dismissed_count, mode,
    )
    if code != EXIT_OK:
        return code

    # CI gate: --fail-on <severity> exits 1 when at-or-above findings exist.
    fail_on = getattr(args, "fail_on", "none")
    if fail_on and fail_on != "none":
        threshold = SEVERITY_ORDER.index(fail_on)
        failing = sum(by_severity.get(s, 0) for s in SEVERITY_ORDER[: threshold + 1])
        if failing:
            print(f"\n❌ Gate failed: {failing} finding(s) at or above '{fail_on}'")
            return EXIT_GATE_FAILED
        print(f"\n✅ Gate passed: no findings at or above '{fail_on}'")

    if findings and mode == "full" and not (args.sarif or args.json_out or args.markdown):
        print(f"\n💡 Tip: jambu share <id> to generate a shareable link")
    return EXIT_OK


def cmd_quick(args) -> int:
    return cmd_audit(args, mode="quick")


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
    print(f"   JSON:  {url}")
    print(f"   Report: {url}/report   (HTML, print-friendly)")
    print(f"\n   Anyone with this link can view the audit results.")


def cmd_report(args) -> int:
    """Download the self-contained HTML report for a saved audit."""
    if not args.audit_id:
        print("Usage: jambu report <audit-id> [--out FILE]")
        return EXIT_OK

    html = api_request_text(f"/audit/report/{args.audit_id}")
    if html is None:
        return EXIT_ENGINE_ERROR

    if args.out == "-":
        print(html)
        return EXIT_OK

    out = Path(args.out) if args.out else Path(f"jambu-report-{args.audit_id}.html")
    out.write_text(html, encoding="utf-8")
    print(f"\n📄 Report written to {out}")
    print("   Open it in a browser, or print to PDF (⌘P / Ctrl+P → Save as PDF).")
    return EXIT_OK


def get_dcm_url() -> str:
    return os.environ.get("JAMBU_DCM_URL", "http://127.0.0.1:3001")


def _dcm_request(method: str, path: str, data: dict = None, timeout: float = 30.0):
    """Call a DCM node directly (no engine needed). Returns (status, body)."""
    url = get_dcm_url().rstrip("/") + path
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"}
    auth = os.environ.get("JAMBU_DCM_AUTH", "")
    if auth:
        headers["Authorization"] = auth
    req = Request(url, data=body, headers=headers, method=method)
    try:
        resp = urlopen(req, timeout=timeout)
        raw = resp.read()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw.decode(errors="replace")
    except HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except URLError as e:
        print(f"\033[91mError: Cannot reach DCM node at {get_dcm_url()} ({e.reason})\033[0m")
        print("Start one: cd decentracode/backend && npm start")
        return 0, None


def cmd_dcm(args) -> int:
    """Operate a DecentraCode Mesh (DCM) node from the terminal."""
    sub = getattr(args, "dcm_command", None)
    if sub is None:
        print("Usage: jambu dcm {status,infer} ...")
        print("       jambu dcm status                    Node, mesh and model overview")
        print("       jambu dcm infer <prompt> [--model M] [--max-tokens N]")
        print("Set JAMBU_DCM_URL to point at a node (default http://127.0.0.1:3001).")
        return EXIT_OK

    if sub == "status":
        status, health = _dcm_request("GET", "/health", timeout=5.0)
        if status == 0:
            return EXIT_ENGINE_ERROR
        print(f"\n🕸  DecentraCode node — {get_dcm_url()}")
        print(f"   Health: {'ok' if status == 200 else f'HTTP {status}'}")

        _, inf = _dcm_request("GET", "/api/inference/status", timeout=10.0)
        if isinstance(inf, dict):
            top_ready = bool(inf.get("engine_ready", inf.get("ready")))
            moe = inf.get("moe") or {}
            moe_ready = bool(moe.get("ready") or moe.get("available"))
            # A top-level error only concerns the *default* runtime — a ready
            # secondary (MoE sidecar) means the node can still serve inference.
            if top_ready or moe_ready:
                if top_ready:
                    line = f"{inf.get('runtime', 'runtime')} ready"
                else:
                    line = f"{moe.get('runtime', 'MoE')} ready"
                    if inf.get("error"):
                        line += f" ({inf.get('runtime', 'default')}: {str(inf['error'])[:60]})"
            else:
                line = "not ready — " + str(inf.get("error") or "no runtime available")[:80]
            print(f"   Inference: {line}")
        else:
            print(f"   Inference: unavailable — {str(inf)[:100]}")

        _, models = _dcm_request("GET", "/api/models", timeout=10.0)
        model_list = models.get("models", []) if isinstance(models, dict) else []
        if model_list:
            available = [
                m.get("id") for m in model_list
                if m.get("available") or m.get("status") in ("available", "ready")
            ]
            print(f"   Models: {len(available)} available"
                  + (f" — {', '.join(str(a) for a in available[:4])}" if available else ""))

        _, mesh = _dcm_request("GET", "/api/network/status", timeout=10.0)
        if isinstance(mesh, dict) and not mesh.get("error"):
            peers = mesh.get("peers") or mesh.get("peer_count") or []
            n = len(peers) if isinstance(peers, (list, dict)) else peers
            print(f"   Mesh: {n} peer(s) · node {str(mesh.get('nodeId') or mesh.get('node_id') or '?')[:16]}")
        print()
        return EXIT_OK

    if sub == "infer":
        prompt = " ".join(args.prompt) if isinstance(args.prompt, list) else args.prompt
        prompt = (prompt or "").strip()
        if not prompt:
            print("Usage: jambu dcm infer <prompt> [--model M] [--max-tokens N]")
            return EXIT_OK
        status, resp = _dcm_request(
            "POST", "/api/inference/v1/chat/completions",
            {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": args.max_tokens,
                "stream": False,
                **({"model": args.model} if args.model else {}),
            },
            timeout=120.0,
        )
        if status == 0:
            return EXIT_ENGINE_ERROR
        if status != 200:
            detail = resp.get("error") if isinstance(resp, dict) else str(resp)[:200]
            code = resp.get("code") if isinstance(resp, dict) else ""
            print(f"\033[91m❌ DCM inference failed ({status}{f' {code}' if code else ''}): {detail}\033[0m")
            return EXIT_ENGINE_ERROR

        choice = (resp.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        usage = resp.get("usage") or {}
        print(f"\n🕸  DCM · {resp.get('model', '?')}\n")
        print(content.strip() or "(empty response)")
        print(f"\n   {usage.get('completion_tokens', 0)} completion tokens"
              + (f" · {usage.get('total_ms', 0) / 1000:.1f}s" if usage.get("total_ms") else ""))
        return EXIT_OK

    print(f"Unknown dcm subcommand: {sub}")
    return EXIT_OK


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
        online_statuses = ("ok", "online", "ready", "healthy")
        print(f"    {_ok_icon(str(status).lower() in online_statuses)} status: {status}")
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

    listing = api_request("GET", f"/mission/{args.mission_id}/results?limit=2")
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


def _fmt_ts(value) -> str:
    """Render an epoch timestamp as a short local date-time."""
    if not value:
        return "never"
    try:
        import datetime
        return datetime.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return str(value)


def _run_summary_line(run: dict) -> str:
    parts = [
        f"{run.get('total_findings', 0)} findings",
        f"+{run.get('new_findings', 0)} new",
        f"-{run.get('resolved_findings', 0)} resolved",
    ]
    if run.get("visual_change_pct") is not None:
        parts.append(f"visual {run['visual_change_pct']:.2f}%")
    if run.get("visual_changed"):
        parts.append("VISUAL CHANGE")
    if run.get("baseline"):
        parts.append("baseline")
    if run.get("alerted"):
        parts.append("ALERTED")
    if run.get("status") == "error":
        parts.append(f"error: {str(run.get('error', '?'))[:80]}")
    return ", ".join(parts)


def cmd_monitor(args) -> int:
    """Manage recurring audit monitors (regression alerts)."""
    sub = getattr(args, "monitor_command", None)
    if sub is None:
        print("Usage: jambu monitor {add,list,rm,run,runs,screenshot,diff} ...")
        print("       jambu monitor add <url> [--interval 60] [--mode quick|full]")
        print("                                [--fail-on high] [--webhook URL] [--run-now]")
        print("       jambu monitor screenshot <monitor-id> <run-id> [--out shot.png]")
        print("       jambu monitor diff <monitor-id> <run-id> [--out diff.png]")
        return EXIT_OK

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
        resp = api_request("POST", "/audit/monitors", payload)
        if not resp:
            return EXIT_ENGINE_ERROR
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
            print(f"\n   Baseline run: {_run_summary_line(run)}")
            if run.get("alert_findings"):
                for f in run["alert_findings"]:
                    print(f"      [{f.get('severity', '?').upper()}] {f.get('title', '?')}")
        return EXIT_OK

    if sub == "list":
        resp = api_request("GET", "/audit/monitors")
        if not resp:
            return EXIT_ENGINE_ERROR
        monitors = resp.get("monitors", [])
        if not monitors:
            print("No monitors yet. Create one: jambu monitor add https://example.com")
            return EXIT_OK
        print(f"\n🛰  Audit Monitors ({len(monitors)})\n")
        print(f"{'ID':>3}  {'State':>5}  {'Every':>7}  {'Alert':>8}  {'Last':<16}  {'Findings':>8}  URL")
        print(f"{'─' * 100}")
        for m in monitors:
            state = "on" if m.get("enabled") else "off"
            last = _fmt_ts(m.get("last_run_at"))
            findings = m.get("last_finding_count")
            findings_str = "-" if findings is None else str(findings)
            print(f"{m['id']:>3}  {state:>5}  {m['interval_minutes']:>5}m  "
                  f"{m['fail_on']:>8}  {last:<16}  {findings_str:>8}  {m['url']}")
        return EXIT_OK

    if sub == "rm":
        resp = api_request("DELETE", f"/audit/monitors/{args.monitor_id}")
        if not resp:
            return EXIT_ENGINE_ERROR
        print(f"✓ Monitor #{args.monitor_id} deleted")
        return EXIT_OK

    if sub == "run":
        resp = api_request("POST", f"/audit/monitors/{args.monitor_id}/run")
        if not resp:
            return EXIT_ENGINE_ERROR
        if resp.get("status") == "error":
            print(f"❌ Monitor #{args.monitor_id} run failed: {resp.get('error')}")
            return EXIT_ENGINE_ERROR
        print(f"\n🛰  Monitor #{args.monitor_id} — {resp.get('url')}")
        print(f"   {_run_summary_line(resp)}")
        if resp.get("alert_findings"):
            print("   New findings requiring attention:")
            for f in resp["alert_findings"]:
                print(f"      [{f.get('severity', '?').upper()}] {f.get('title', '?')}")
        return EXIT_OK

    if sub == "runs":
        resp = api_request("GET",
                           f"/audit/monitors/{args.monitor_id}/runs?limit={args.limit}")
        if not resp:
            return EXIT_ENGINE_ERROR
        runs = resp.get("runs", [])
        if not runs:
            print(f"Monitor #{args.monitor_id} has no runs yet "
                  f"(try: jambu monitor run {args.monitor_id})")
            return EXIT_OK
        print(f"\n🛰  Monitor #{args.monitor_id} — {len(runs)} run(s)\n")
        for r in runs:
            print(f"   {_fmt_ts(r.get('run_at'))}  {_run_summary_line(r)}")
        return EXIT_OK

    if sub == "screenshot":
        data = api_request_bytes(
            f"/audit/monitors/{args.monitor_id}/runs/{args.run_id}/screenshot"
        )
        if not data:
            return EXIT_ENGINE_ERROR
        out = args.out or f"monitor-{args.monitor_id}-run-{args.run_id}.png"
        path = Path(out)
        if str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"✓ Screenshot written to {path} ({len(data)} bytes)")
        return EXIT_OK

    if sub == "diff":
        data = api_request_bytes(
            f"/audit/monitors/{args.monitor_id}/runs/{args.run_id}/diff"
        )
        if not data:
            return EXIT_ENGINE_ERROR
        out = args.out or f"monitor-{args.monitor_id}-run-{args.run_id}-diff.png"
        path = Path(out)
        if str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"✓ Diff image written to {path} ({len(data)} bytes)")
        print("  Red pixels changed since the previous run; the rest is dimmed.")
        return EXIT_OK

    print(f"Unknown monitor subcommand: {sub}")
    return EXIT_OK


def _load_flow_file(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return {"steps": data}
    return data


def _snapshot_mtimes(paths: list[str]) -> dict[str, float]:
    """Map every watched file to its mtime (dirs walked, junk skipped)."""
    import os as _os

    skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "dist",
                 "build", ".next", ".nuxt", "coverage", ".pytest_cache"}
    out: dict[str, float] = {}
    for base in paths or []:
        if not base:
            continue
        if _os.path.isfile(base):
            try:
                out[base] = _os.path.getmtime(base)
            except OSError:
                pass
        elif _os.path.isdir(base):
            for root, dirs, files in _os.walk(base):
                dirs[:] = [d for d in dirs
                           if d not in skip_dirs and not d.startswith(".")]
                for name in files:
                    if name.startswith(".") or name.endswith((".pyc", ".log")):
                        continue
                    full = _os.path.join(root, name)
                    try:
                        out[full] = _os.path.getmtime(full)
                    except OSError:
                        pass
    return out


def _changed_files(before: dict[str, float], after: dict[str, float]) -> list[str]:
    changed = [p for p, m in after.items() if before.get(p) != m]
    changed += [p for p in before if p not in after]
    return sorted(changed)


def cmd_watch(args) -> int:
    """Rerun a flow whenever watched files change (dev-loop staple)."""
    import argparse as _argparse

    watch_paths = [args.flow] + list(args.watch_dir or [])
    test_args = _argparse.Namespace(
        url=args.url, flow=args.flow, local=args.local, approve=args.approve,
        stop_on_failure=args.stop_on_failure, trace=args.trace, har=args.har,
        video=args.video, resolve_sources=args.resolve_sources, json=args.json,
        forbid_evaluate=args.forbid_evaluate,
    )

    def run_once() -> int:
        # Reload the flow from disk each run so edits apply immediately.
        try:
            _load_flow_file(args.flow)
        except Exception as exc:
            print(f"Could not read flow file {args.flow}: {exc}")
            return EXIT_ENGINE_ERROR
        print(f"--- jambu watch: {time.strftime('%H:%M:%S')} ---")
        return cmd_test(test_args)

    if args.once:
        return run_once()

    print(f"Watching {len(watch_paths)} path(s), every {args.interval:g}s "
          f"(Ctrl-C to stop).")
    last = _snapshot_mtimes(watch_paths)
    code = run_once()
    if code not in (EXIT_OK, EXIT_GATE_FAILED):
        print("(engine unreachable — will keep retrying on change)")
    try:
        while True:
            time.sleep(args.interval)
            current = _snapshot_mtimes(watch_paths)
            changed = _changed_files(last, current)
            last = current
            if not changed:
                continue
            print(f"changed: {', '.join(changed[:5])}"
                  + (f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""))
            cmd_test(test_args)
    except KeyboardInterrupt:
        print("\nStopped watching.")
    return EXIT_OK


def cmd_test(args) -> int:
    """Run a browser test flow (local dev friendly) and report pass/fail."""
    flow: dict = {}
    if getattr(args, "flow", None):
        try:
            flow = _load_flow_file(args.flow)
        except Exception as exc:
            print(f"Could not read flow file {args.flow}: {exc}")
            return EXIT_ENGINE_ERROR
    url = args.url or flow.get("url")
    if not url:
        print("Provide --url or include an 'url' in the flow file.")
        return EXIT_ENGINE_ERROR
    steps = flow.get("steps") or []
    if args.url and steps and not any(s.get("action") == "navigate" for s in steps):
        steps = [{"action": "navigate", "url": args.url}] + steps
    payload = {
        "url": url,
        "steps": steps,
        "local": args.local,
        "approve": args.approve,
        "stop_on_failure": args.stop_on_failure,
        "trace": args.trace,
        "har": args.har,
        "video": args.video,
        "resolve_sources": args.resolve_sources,
        "forbid_evaluate": args.forbid_evaluate,
        "network": flow.get("network"),
    }
    result = api_request("POST", "/browser/sessions/run", payload)
    if result is None:
        return EXIT_ENGINE_ERROR
    if "error" in result:
        print(f"Test flow failed: {result['error']}")
        return EXIT_ENGINE_ERROR

    status = "PASS" if result.get("ok") else "FAIL"
    print(f"Browser test {status} — {result.get('passed', 0)}/{result.get('total', 0)} steps "
          f"in {result.get('duration_ms', 0)}ms")
    print(f"  final: {result.get('title', '') or '(untitled)'} — {result.get('final_url', '')}")
    for step in result.get("steps") or []:
        mark = "ok " if step.get("status") == "passed" else "FAIL"
        line = f"  {mark} #{step.get('i')} {step.get('action')}"
        if step.get("detail"):
            line += f" — {step['detail']}"
        if step.get("status") == "failed":
            line += f" — {step.get('reason')}: {step.get('error')}"
        print(line)
    for err in (result.get("console_errors") or [])[:5]:
        print(f"  console: {err[:160]}")
    for bad in (result.get("bad_responses") or [])[:5]:
        print(f"  http {bad.get('status')}: {bad.get('url', '')[:120]}")
    artifacts = result.get("artifacts") or {}
    if artifacts:
        print("  artifacts: " + ", ".join(f"{k}={v}" for k, v in artifacts.items()))
    if args.json:
        print(json.dumps(result, indent=2))
    return EXIT_OK if result.get("ok") else EXIT_GATE_FAILED


def cmd_export(args) -> int:
    """Export a flow to Playwright Test source (or JSON)."""
    try:
        flow = _load_flow_file(args.flow)
    except Exception as exc:
        print(f"Could not read flow file {args.flow}: {exc}")
        return EXIT_ENGINE_ERROR
    steps = flow.get("steps") or []
    url = args.url or flow.get("url") or ""
    if args.json:
        print(json.dumps({"steps": steps}, indent=2))
        return EXIT_OK
    result = api_request("POST", "/browser/sessions/export", {
        "steps": steps, "name": args.name or "jambubrowser flow",
        "url": url, "base_url": args.base_url or "",
    })
    if result is None:
        return EXIT_ENGINE_ERROR
    if "error" in result:
        print(f"Export failed: {result['error']}")
        return EXIT_ENGINE_ERROR
    code = result.get("code", "")
    if args.out:
        Path(args.out).write_text(code)
        print(f"Wrote {args.out}")
    else:
        print(code)
    return EXIT_OK


def cmd_import(args) -> int:
    """Convert a Playwright .spec.ts into a Jambubrowser flow JSON."""
    try:
        code = Path(args.spec).read_text()
    except Exception as exc:
        print(f"Could not read {args.spec}: {exc}")
        return EXIT_ENGINE_ERROR
    result = api_request("POST", "/browser/sessions/import", {"code": code})
    if result is None or "error" in result:
        print(f"Import failed: {(result or {}).get('error', 'engine unreachable')}")
        return EXIT_ENGINE_ERROR
    doc = {"url": args.url or "", "steps": result.get("steps") or []}
    unparsed = result.get("unparsed") or []
    if args.out:
        Path(args.out).write_text(json.dumps(doc, indent=2))
        print(f"Wrote {args.out} ({len(doc['steps'])} steps)")
    else:
        print(json.dumps(doc, indent=2))
    if unparsed:
        print(f"unparsed lines: {len(unparsed)}")
        for item in unparsed[:10]:
            print(f"  line {item.get('line')}: {item.get('text')}")
    return EXIT_OK


def cmd_plan(args) -> int:
    """Propose a test flow from a natural-language goal."""
    result = api_request("POST", "/browser/sessions/plan", {
        "url": args.url, "goal": " ".join(args.goal),
        "kind": args.kind or None, "use_llm": args.use_llm,
    })
    if result is None:
        return EXIT_ENGINE_ERROR
    if "error" in result:
        print(f"Plan failed: {result['error']}")
        return EXIT_ENGINE_ERROR
    print(f"# {result.get('kind')} plan ({result.get('source')})")
    if result.get("placeholders"):
        print(f"fill: {', '.join(result['placeholders'])}")
    print(json.dumps(result.get("steps") or [], indent=2))
    return EXIT_OK


def cmd_record(args) -> int:
    """Start/stop recording a session's actions into a reusable flow."""
    session_id = args.session
    if args.stop:
        result = api_request("POST", f"/browser/sessions/{session_id}/record",
                             {"active": False})
        if result is None or "error" in result:
            print(f"Stop recording failed: {(result or {}).get('error', 'engine unreachable')}")
            return EXIT_ENGINE_ERROR
        steps = result.get("steps") or []
        doc = {"url": args.url or "", "steps": steps}
        if args.out:
            Path(args.out).write_text(json.dumps(doc, indent=2))
            print(f"Wrote {args.out} ({len(steps)} steps)")
        else:
            print(json.dumps(doc, indent=2))
        return EXIT_OK

    result = api_request("POST", f"/browser/sessions/{session_id}/record",
                         {"active": True})
    if result is None or "error" in result:
        print(f"Start recording failed: {(result or {}).get('error', 'engine unreachable')}")
        return EXIT_ENGINE_ERROR
    print(f"Recording session {session_id}. Drive the browser, then save with:")
    print(f"  jambu record --session {session_id} --stop --out flow.json")
    return EXIT_OK


def cmd_dev_servers(args) -> int:
    """Scan common ports for a running local dev server."""
    from urllib.parse import quote

    result = api_request("GET", f"/browser/dev-servers?host={quote(args.host)}")
    if result is None or "error" in result:
        print(f"Scan failed: {(result or {}).get('error', 'engine unreachable')}")
        return EXIT_ENGINE_ERROR
    servers = result.get("servers") or []
    if not servers:
        print(f"No dev servers found on {args.host} (scanned common ports).")
        return EXIT_GATE_FAILED
    for server in servers:
        print(f"  :{server.get('port')}  {server.get('framework') or 'unknown'}  "
              f"{server.get('title') or server.get('url')}")
    return EXIT_OK


def _add_audit_options(p: argparse.ArgumentParser) -> None:
    """Options shared by `audit` and `quick` (exports + CI gate)."""
    p.add_argument("url", help="URL to audit")
    p.add_argument(
        "--sarif", metavar="FILE",
        help="Write SARIF 2.1.0 results for GitHub code scanning ('-' = stdout)",
    )
    p.add_argument(
        "--json", dest="json_out", metavar="FILE",
        help="Write canonical JSON results ('-' = stdout)",
    )
    p.add_argument(
        "--markdown", metavar="FILE",
        help="Write a human-readable Markdown report ('-' = stdout)",
    )
    p.add_argument(
        "--fail-on", dest="fail_on",
        choices=["critical", "high", "medium", "low", "none"],
        default="none",
        help="Exit 1 when a finding at or above this severity exists "
             "(default: none — never fail)",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="jambu",
        description="Jambubrowser CLI — AI-powered webapp auditing",
    )
    subparsers = parser.add_subparsers(dest="command")

    p_auth = subparsers.add_parser("auth", help="Set API key")
    p_auth.add_argument("api_key", nargs="?", help="Your API key (jambu_...)")

    p_audit = subparsers.add_parser(
        "audit",
        help="Full audit (6 employees) with optional SARIF/JSON/Markdown export",
    )
    _add_audit_options(p_audit)

    p_quick = subparsers.add_parser(
        "quick",
        help="Quick scan (3 employees) with optional SARIF/JSON/Markdown export",
    )
    _add_audit_options(p_quick)

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

    p_test = subparsers.add_parser(
        "test", help="Run a browser test flow (local-dev friendly)",
    )
    p_test.add_argument("flow", nargs="?", help="Flow JSON file")
    p_test.add_argument("--url", help="App URL (or from the flow file)")
    p_test.add_argument("--local", action="store_true",
                        help="Allow localhost/private hosts")
    p_test.add_argument("--approve", action="store_true",
                        help="Approve risky/input actions")
    p_test.add_argument("--stop-on-failure", dest="stop_on_failure",
                        action="store_true")
    p_test.add_argument("--trace", action="store_true", help="Capture a trace")
    p_test.add_argument("--har", action="store_true", help="Capture a HAR")
    p_test.add_argument("--video", action="store_true", help="Record video")
    p_test.add_argument("--resolve-sources", dest="resolve_sources",
                        action="store_true", help="Map console errors via source maps")
    p_test.add_argument("--forbid-evaluate", dest="forbid_evaluate",
                        action="store_true",
                        help="Refuse JS-dependent evaluate steps")
    p_test.add_argument("--json", action="store_true", help="Print the full report JSON")

    p_export = subparsers.add_parser(
        "export", help="Export a flow to Playwright Test (.spec.ts)",
    )
    p_export.add_argument("flow", help="Flow JSON file")
    p_export.add_argument("--name", help="Test name")
    p_export.add_argument("--url", help="Original entry URL (kept as a comment)")
    p_export.add_argument("--base-url", dest="base_url", help="Playwright baseURL")
    p_export.add_argument("--out", help="Output .spec.ts path")
    p_export.add_argument("--json", action="store_true",
                          help="Emit normalised flow JSON instead")

    p_import = subparsers.add_parser(
        "import", help="Convert a Playwright .spec.ts into a flow JSON",
    )
    p_import.add_argument("spec", help="Playwright .spec.ts file")
    p_import.add_argument("--out", help="Output flow JSON path")
    p_import.add_argument("--url", help="Entry URL to store with the flow")

    p_plan = subparsers.add_parser(
        "plan", help="Propose a test flow from a natural-language goal",
    )
    p_plan.add_argument("goal", nargs="+", help="e.g. test login")
    p_plan.add_argument("--url", required=True, help="App URL")
    p_plan.add_argument("--kind",
                        help="smoke|login|signup|checkout|search|accessibility|performance|responsive")
    p_plan.add_argument("--use-llm", dest="use_llm", action="store_true")

    p_record = subparsers.add_parser(
        "record", help="Record a session's actions into a reusable flow",
    )
    p_record.add_argument("--session", required=True, help="Browser session id")
    p_record.add_argument("--stop", action="store_true",
                          help="Stop recording and save the flow")
    p_record.add_argument("--out", help="Output flow JSON path")
    p_record.add_argument("--url", help="Entry URL to store with the flow")

    p_watch = subparsers.add_parser(
        "watch", help="Rerun a flow whenever watched files change",
    )
    p_watch.add_argument("flow", help="Flow JSON file")
    p_watch.add_argument("--url", help="App URL (or from the flow file)")
    p_watch.add_argument("--watch-dir", dest="watch_dir", action="append",
                         default=[],
                         help="Extra dir to watch (repeatable; default: the flow file)")
    p_watch.add_argument("--interval", type=float, default=2.0,
                         help="Poll interval in seconds (default 2)")
    p_watch.add_argument("--local", action="store_true",
                         help="Allow localhost/private hosts")
    p_watch.add_argument("--approve", action="store_true",
                         help="Approve risky/input actions")
    p_watch.add_argument("--stop-on-failure", dest="stop_on_failure",
                         action="store_true")
    p_watch.add_argument("--trace", action="store_true")
    p_watch.add_argument("--har", action="store_true")
    p_watch.add_argument("--video", action="store_true")
    p_watch.add_argument("--resolve-sources", dest="resolve_sources",
                         action="store_true")
    p_watch.add_argument("--forbid-evaluate", dest="forbid_evaluate",
                         action="store_true",
                         help="Refuse JS-dependent evaluate steps")
    p_watch.add_argument("--json", action="store_true",
                         help="Print the full report JSON each run")
    p_watch.add_argument("--once", action="store_true",
                         help="Run once and exit (no watching)")

    p_devs = subparsers.add_parser(
        "dev-servers", help="Scan for a running local dev server",
    )
    p_devs.add_argument("--host", default="127.0.0.1")

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

    p_dcm = subparsers.add_parser(
        "dcm", help="Operate a DecentraCode Mesh node (status, infer)",
    )
    d_sub = p_dcm.add_subparsers(dest="dcm_command")
    d_sub.add_parser("status", help="Node, mesh, and model overview")
    p_d_infer = d_sub.add_parser("infer", help="Run a prompt on the mesh")
    p_d_infer.add_argument("prompt", nargs="+", help="Prompt text")
    p_d_infer.add_argument("--model", default=None,
                           help="DCM model id (default: node default)")
    p_d_infer.add_argument("--max-tokens", dest="max_tokens", type=int,
                           default=64)

    args = parser.parse_args()

    code = EXIT_OK
    if args.command == "auth":
        cmd_auth(args)
    elif args.command == "audit":
        code = cmd_audit(args)
    elif args.command == "quick":
        code = cmd_quick(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "share":
        cmd_share(args)
    elif args.command == "report":
        code = cmd_report(args)
    elif args.command == "tiers":
        cmd_tiers(args)
    elif args.command == "health":
        cmd_health(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "diff":
        cmd_diff(args)
    elif args.command == "test":
        code = cmd_test(args)
    elif args.command == "export":
        code = cmd_export(args)
    elif args.command == "import":
        code = cmd_import(args)
    elif args.command == "plan":
        code = cmd_plan(args)
    elif args.command == "record":
        code = cmd_record(args)
    elif args.command == "watch":
        code = cmd_watch(args)
    elif args.command == "dev-servers":
        code = cmd_dev_servers(args)
    elif args.command == "monitor":
        code = cmd_monitor(args)
    elif args.command == "dcm":
        code = cmd_dcm(args)
    else:
        parser.print_help()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
