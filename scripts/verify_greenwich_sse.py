#!/usr/bin/env python3
"""Greenwich §6 verification — SSE telemetry over real HTTP (uvicorn).

Runs the engine in-process on :8099 and consumes
``/browser/sessions/{id}/events`` with a real socket, then drives a
navigation and an emitted ``audit:step`` through the bus.

Usage:  .venv/bin/python scripts/verify_greenwich_sse.py
"""
import asyncio
import os
import sys
import threading
import time

ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
FIX = "/tmp/greenwich_verify"
os.environ["ALLOW_LOCAL_WORKSPACES"] = "true"
os.environ["JAMBU_LOCAL_WORKSPACE_ROOTS"] = FIX
os.environ["JAMBU_BROWSER_IDLE_TTL"] = "300"

os.makedirs(FIX, exist_ok=True)
_FIXTURE = os.path.join(FIX, "page.html")
if not os.path.exists(_FIXTURE):
    with open(_FIXTURE, "w") as _fh:
        _fh.write(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Agathiyar Gnanavettiyan Archive</title></head><body>"
            "<h1>Agathiyar Gnanavettiyan Archive</h1>"
            "<input id='q' placeholder='Search manuscripts...'>"
            "<div id='out'>idle</div></body></html>"
        )

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import backend.engine as eng  # noqa: E402
from backend.engine_runtime import manager  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)[:300]))
    print(("PASS" if cond else "FAIL") + " | " + name + ((" | " + str(detail)[:300]) if detail else ""), flush=True)


# Capture the server loop so cross-thread emits are thread-safe.
loop_holder = {}
_orig_start = eng.start_subsystems


async def _patched_start():
    loop_holder["loop"] = asyncio.get_running_loop()
    return await _orig_start()


eng.start_subsystems = _patched_start

PORT = 8099
server = uvicorn.Server(uvicorn.Config(eng.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()

base = f"http://127.0.0.1:{PORT}"
ready = False
for _ in range(120):
    try:
        if httpx.get(base + "/health", timeout=0.5).status_code in (200, 404):
            ready = True
            break
    except Exception:
        time.sleep(0.25)
check("S0.uvicorn engine ready", ready)

client = httpx.Client(base_url=base, timeout=30.0)
r = client.post("/browser/sessions", json={
    "allow_domains": ["127.0.0.1"], "require_approval": False,
    "allow_private": True, "scrub_pii": False})
sid = r.json()["session_id"]
check("S1.session open", r.status_code == 200, r.text[:120])

lines = []
status = {}


def consume():
    try:
        with client.stream("GET", f"/browser/sessions/{sid}/events") as resp:
            status["code"] = resp.status_code
            status["ct"] = resp.headers.get("content-type", "")
            for line in resp.iter_lines():
                lines.append(line)
                if len(lines) > 120:
                    break
    except Exception as exc:
        lines.append(f"ERR:{exc!r}")


th = threading.Thread(target=consume, daemon=True)
th.start()
time.sleep(1.0)

file_url = "file://" + FIX + "/page.html"
client.post(f"/browser/sessions/{sid}/navigate", json={"url": file_url})
time.sleep(1.0)

# audit:step emitted through the same bus the audit route uses (thread-safe hop)
asyncio.run_coroutine_threadsafe(
    manager.emit_session_event(sid, "audit:step",
                               {"agent": "security", "finding": "demo finding", "severity": "medium"}),
    loop_holder["loop"],
).result(timeout=5)
time.sleep(1.0)
client.post(f"/browser/sessions/{sid}/navigate", json={"url": file_url})
time.sleep(1.0)

client.post(f"/browser/sessions/{sid}/navigate", json={"url": "https://not-allowlisted.example/"})
time.sleep(1.0)

blob = "\n".join(lines)
check("S2.SSE 200 + content-type", status.get("code") == 200 and status.get("ct", "").startswith("text/event-stream"), status)
check("S3.SSE ready event", "event: ready" in blob, blob[:120])
check("S4.nav:start", "nav:start" in blob)
check("S5.nav:domcontentloaded", "nav:domcontentloaded" in blob)
check("S6.nav:networkidle", "nav:networkidle" in blob)
check("S7.audit:step fan-out", "audit:step" in blob and "security" in blob)
check("S9.error event on blocked navigation", "event: error" in blob and "not-allowlisted.example" in blob, blob[-260:])
check("S8.SSE structured data lines", '"data":' in blob and '"timestamp":' in blob, blob[:200])

client.delete(f"/browser/sessions/{sid}")
client.close()
server.should_exit = True
time.sleep(1.5)

fails = [r for r in RESULTS if not r[1]]
print(f"\n=== SSE SUMMARY: {len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed ===")
for name, ok, detail in fails:
    print("FAILED:", name, "|", detail)
sys.exit(1 if fails else 0)
