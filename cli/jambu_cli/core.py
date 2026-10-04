"""Shared plumbing for every `jambu` command: config, engine HTTP, exit codes
and the formatters commands print with.

This is the only module in the CLI that touches the network or the filesystem.
Commands reach it as `core.api_request(...)` — attribute access, not a
from-import — so `jambu.api_request` stays the single patch point for tests and
for anyone embedding the CLI, which is what patching one name used to mean.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
        print(f"Start the engine: .venv/bin/python -m uvicorn backend.engine:app --port 8001")
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

def _json_load_arg(raw: str):
    """Parse a JSON CLI argument, warning (not crashing) on bad input."""
    import json as _json

    try:
        return _json.loads(raw)
    except Exception as exc:
        print(f"Ignoring invalid JSON argument ({exc})")
        return None

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

def _section(title: str):
    print(f"\n  {title}")
    print(f"  {'─' * max(0, 60 - len(title))}")

def _ok_icon(ok: bool) -> str:
    return "✓" if ok else "✗"

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
        "--jira", metavar="FILE",
        help="Write Jira issue-create payloads ('-' = stdout)",
    )
    p.add_argument(
        "--linear", metavar="FILE",
        help="Write Linear issue-create payloads ('-' = stdout)",
    )
    p.add_argument(
        "--fail-on", dest="fail_on",
        choices=["critical", "high", "medium", "low", "none"],
        default="none",
        help="Exit 1 when a finding at or above this severity exists "
             "(default: none — never fail)",
    )
