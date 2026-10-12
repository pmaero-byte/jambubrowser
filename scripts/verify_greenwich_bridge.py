#!/usr/bin/env python3
"""Live verification of the 7 Greenwich bridge improvements."""
import asyncio
import base64
import base64
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
FIX = "/tmp/greenwich_verify"
os.environ["ALLOW_LOCAL_WORKSPACES"] = "true"
os.environ["JAMBU_LOCAL_WORKSPACE_ROOTS"] = FIX


FIXTURE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Agathiyar Gnanavettiyan Archive</title>
<style>#probe { position: absolute; top: 10px; left: 10px; width: 200px; height: 60px; }</style></head>
<body>
  <img id="px" src="pixel.png" alt="pixel" width="8" height="8">
  <h1>Agathiyar Gnanavettiyan Archive</h1>
  <input id="q" placeholder="Search manuscripts...">
  <button id="submit" onclick="document.getElementById('out').textContent='CLICKED'">Submit Search</button>
  <a href="/verse/1">Verse 0001 Commentary</a>
  <div id="out">idle</div>
  <button id="probe" onclick="document.getElementById('out').textContent='PROBED'">Probe</button>
  <script>
    document.getElementById('q').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { document.getElementById('out').textContent = 'SEARCH:' + e.target.value; }
    });
  </script>
</body></html>
"""
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def ensure_fixture(fix_dir: str) -> None:
    """Write the local preview page the browser checks navigate to."""
    os.makedirs(fix_dir, exist_ok=True)
    html = os.path.join(fix_dir, "page.html")
    if not os.path.exists(html):
        with open(html, "w") as fh:
            fh.write(FIXTURE_HTML)
    px = os.path.join(fix_dir, "pixel.png")
    if not os.path.exists(px):
        with open(px, "wb") as fh:
            fh.write(PIXEL_PNG)

ensure_fixture(FIX)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)[:400]))
    print(("PASS" if cond else "FAIL") + " | " + name + ((" | " + str(detail)[:400]) if detail else ""))
    sys.stdout.flush()


# ---------------------------------------------------------------- fake stack
class Handler(BaseHTTPRequestHandler):
    tag = "8085"
    captured = []

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._json(200, {"data": [{"id": "qwen3.8-27b"}]})
        else:
            self._json(404, {})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/chat/completions"):
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for piece in ["<think>hidden", "</think>", "HELLO-", self.tag]:
                    frame = {"choices": [{"delta": {"content": piece}}]}
                    self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self._json(200, {
                    "choices": [{"message": {"content": f"<think>hidden</think> HELLO-{self.tag}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
                })
        elif self.path.endswith("/v1/memory/ingest"):
            Handler.captured.append(payload)
            self._json(200, {"ok": True})
        else:
            self._json(404, {})

    def log_message(self, *a):
        pass


def serve(port, tag):
    cls = type(f"H{port}", (Handler,), {"tag": tag})
    srv = ThreadingHTTPServer(("127.0.0.1", port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


srv8085 = serve(18085, "8085")
srv8090 = serve(18090, "8090")
srv8095 = serve(18095, "memory")
time.sleep(0.3)
print("=== fake stack on 18085/18090/18095 (real :8085/:8090/:8095 untouched) ===")


# ============================ Improvement 4: Greenwich LLM =================
def improvement4():
    print("\n=== Improvement 4a: provider contract (fake stack) ===")
    os.environ["GREENWICH_BASE_URL"] = "http://127.0.0.1:18085/v1"
    os.environ["GREENWICH_EXTENDED_URL"] = "http://127.0.0.1:18090/v1"
    from backend.llm.config import LLMConfig
    from backend.llm.providers.greenwich import GreenwichProvider
    from backend.llm.base import ChatMessage, Role, collect_stream

    cfg = LLMConfig.from_env()
    prov = GreenwichProvider(cfg)
    check("4.health(:8085)", asyncio.run(prov.health()) is True)

    msg = [ChatMessage(role=Role.USER, content="ping")]
    resp = asyncio.run(prov.chat(msg))
    check("4.chat strips <think>", resp.content.strip() == "HELLO-8085", resp.content)
    check("4.chat provider/model", resp.provider == "greenwich" and resp.model == "qwen3.8-27b")
    check("4.chat usage parsed", resp.usage.total_tokens == 8, resp.usage)
    check("4.cost is zero", resp.usage.cost_usd == 0.0)

    sresp = asyncio.run(collect_stream(prov.stream(msg)))
    check("4.stream joins deltas", "HELLO-8085" in sresp.content, sresp.content)
    check("4.stream finish_reason", sresp.finish_reason == "stop", sresp.finish_reason)

    os.environ["GREENWICH_ENABLE_THINKING"] = "1"
    try:
        resp2 = asyncio.run(GreenwichProvider(cfg).chat(msg))
        check("4.thinking passthrough", "<think>hidden</think>" in resp2.content, resp2.content)
    finally:
        os.environ.pop("GREENWICH_ENABLE_THINKING", None)

    srv8085.shutdown()
    srv8085.server_close()
    time.sleep(0.3)
    try:
        resp3 = asyncio.run(prov.chat(msg))
        check("4.fallback to extended (:8090)", resp3.content.strip() == "HELLO-8090", resp3.content)
    except Exception as exc:
        check("4.fallback to extended (:8090)", False, repr(exc))

    print("\n=== Improvement 4b: REAL Greenwich stack :8085 / :8090 ===")
    os.environ["GREENWICH_BASE_URL"] = "http://127.0.0.1:8085/v1"
    os.environ["GREENWICH_EXTENDED_URL"] = "http://127.0.0.1:8090/v1"
    real_cfg = LLMConfig.from_env()
    real = GreenwichProvider(real_cfg)
    check("4R.health real :8085 (Qwen 3.8 27B)", asyncio.run(real.health()) is True)
    tiny = [ChatMessage(role=Role.USER, content="Reply with exactly: pong")]
    try:
        rr = asyncio.run(asyncio.wait_for(real.chat(tiny, max_tokens=8, timeout=60.0), timeout=90))
        check("4R.real 27B completion", bool(rr.content.strip()), rr.content[:80])
        check("4R.real usage reported", rr.usage.total_tokens > 0, rr.usage)
    except Exception as exc:
        check("4R.real 27B completion", False, repr(exc))

    os.environ["GREENWICH_BASE_URL"] = "http://127.0.0.1:18099/v1"  # dead port
    prov2 = GreenwichProvider(LLMConfig.from_env())
    try:
        rr2 = asyncio.run(asyncio.wait_for(prov2.chat(tiny, max_tokens=8, timeout=60.0), timeout=90))
        check("4R.fallback reaches real daemon :8090", bool(rr2.content.strip()), rr2.content[:80])
    except Exception as exc:
        check("4R.fallback reaches real daemon :8090", False, repr(exc))
    finally:
        os.environ["GREENWICH_BASE_URL"] = "http://127.0.0.1:8085/v1"


# ============================ Improvement 5: Memory Bridge =================
def improvement5(client, sid):
    print("\n=== Improvement 5a: spec ingest path (fake :18095) ===")
    os.environ["GREENWICH_MEMORY_URL"] = "http://127.0.0.1:18095"
    from backend.modules.greenwich_bridge import ingest_memory, memory_status

    Handler.captured.clear()
    out = asyncio.run(ingest_memory(
        title="Palm Leaf Palmography Vol. 4", clean_markdown="# manuscript\nverse 0001",
        url="https://example.org/siddha/manuscripts", citations=["https://example.org/a"],
        metadata={"session_id": "bs-test", "audit_score": 98.4}))
    check("5.spec endpoint resolved", out.get("ok") is True and out.get("endpoint") == "/v1/memory/ingest", out)
    time.sleep(0.3)
    got = Handler.captured[-1] if Handler.captured else {}
    check("5.spec payload source", got.get("source") == "jambubrowser", got)
    check("5.spec payload url+markdown", got.get("url") == "https://example.org/siddha/manuscripts" and "verse 0001" in (got.get("clean_markdown") or ""))
    check("5.spec payload citations", got.get("citations") == ["https://example.org/a"], got.get("citations"))

    print("\n=== Improvement 5b: REAL Memory Bridge :8095 ===")
    os.environ.pop("GREENWICH_MEMORY_URL", None)
    st = asyncio.run(memory_status())
    check("5R.bridge live (GET /v1/project/memory)", st.get("ok") is True and "total_tokens_ingested" in (st.get("bridge") or {}), {k: str(v)[:60] for k, v in (st.get("bridge") or {}).items() if k in ("project_root", "total_tokens_ingested", "indexed_files")})
    handler_got = {"before": len(Handler.captured)}
    r = client.post(f"/browser/sessions/{sid}/sync-greenwich", json={
        "title": "jambubrowser bridge verification (auto)",
        "clean_markdown": "verification artifact: AX-tree + live viewport checks passed",
        "url": "file:///tmp/greenwich_verify/page.html",
        "citations": ["file:///tmp/greenwich_verify/page.html"],
    })
    body = r.json() if r.status_code == 200 else {}
    check("5R./sync-greenwich route 200", r.status_code == 200, r.text[:200])
    endpoint = (body or {}).get("endpoint")
    # Either route is a pass: /v1/memory/ingest is the spec contract, and
    # /v1/trajectory/record is the older deployed bridge. Which one answered
    # is reported, not asserted, because the daemon evolves independently.
    check("5R.bridge accepted (spec or deployed route)",
          body.get("ok") is True and endpoint in ("/v1/memory/ingest", "/v1/trajectory/record"),
          f"endpoint={endpoint}")


# ============================ Browser-backed: 1, 2, 3, 6, 7 ================
def _mgr_session_steps(sid):
    from backend.modules.browser_agent import get_browser_agent_service
    return get_browser_agent_service().get(sid).steps


def browser_improvements():
    from fastapi.testclient import TestClient
    import backend.engine as eng
    from backend.modules.browser import BrowserManager, shared_browser_info

    file_url = f"file://{FIX}/page.html"
    ref_re = re.compile(r'@(\d+): (\w+) "([^"]*)"')

    with TestClient(eng.app, raise_server_exceptions=False) as client:
        # ---- Improve 7: local workspace navigation ---------------------
        print("\n=== Improvement 7: file:// local workspace ===")
        r = client.post("/browser/sessions", json={
            "allow_domains": ["127.0.0.1", "localhost"],
            "require_approval": False, "allow_private": True, "scrub_pii": False,
        })
        check("7.open session", r.status_code == 200, r.text[:200])
        sid = r.json()["session_id"]

        r = client.post(f"/browser/sessions/{sid}/navigate", json={"url": file_url})
        check("7.navigate file:// allowed", r.status_code == 200, r.text[:200])

        from backend.core.security import is_safe_file_url
        check("7.file:// outside root blocked", is_safe_file_url("file:///etc/passwd") is False)

        # ---- Improve 3: AX tree + act-by-ref ---------------------------
        print("\n=== Improvement 3: AX tree + act-by-ref ===")
        r = client.get(f"/browser/sessions/{sid}/ax-tree")
        tree = r.json().get("ax_tree", "")
        check("3.ax-tree status", r.status_code == 200, r.text[:160])
        check("3.tree root", tree.startswith("[Root: document]"), tree[:120])
        parsed = ref_re.findall(tree)
        roles = {role for _, role, _ in parsed}
        check("3.implicit roles (link/textbox/button)", {"link", "textbox", "button"} <= roles, sorted(roles))
        input_ref = next((int(n) for n, role, name in parsed if role == "textbox"), None)
        button_ref = next((int(n) for n, role, name in parsed if role == "button" and "Submit" in name), None)
        link_ref = next((int(n) for n, role, _ in parsed if role == "link"), None)
        check("3.refs parsed", None not in (input_ref, button_ref, link_ref), f"in={input_ref} btn={button_ref} link={link_ref}")
        check("3.href in tree", 'href="' in tree and "/verse/1" in tree, tree)

        # "Submit Search" trips the risk classifier on purpose: irreversible-sounding
        # elements always require explicit approval, whatever the session policy says.
        r = client.post(f"/browser/sessions/{sid}/act-by-ref", json={"action": "click", "ref": button_ref, "approve": True})
        check("3.click by ref", r.status_code == 200, r.text[:200])
        t = client.get(f"/browser/sessions/{sid}/snapshot").json().get("text", "")
        check("3.click effect (DOM changed)", "CLICKED" in t, t.replace("\n", " ")[:120])

        tamil = "அகத்தியர் ஞானவெட்டியான்"
        r = client.post(f"/browser/sessions/{sid}/act-by-ref", json={
            "action": "type", "ref": input_ref, "text": tamil, "press_enter": True})
        check("3.type+Enter by ref", r.status_code == 200, r.text[:200])
        time.sleep(0.4)
        t = client.get(f"/browser/sessions/{sid}/snapshot").json().get("text", "")
        check("3.Enter submitted search", ("SEARCH:" in t) and ("அகத்தியர்" in t), t.replace("\n", " ")[:140])

        r = client.get(f"/browser/sessions/{sid}/ax-tree")
        check("3.out-of-range ref 400/403", client.post(f"/browser/sessions/{sid}/act-by-ref", json={"action": "click", "ref": 999}).status_code == 403)

        # ---- Improve 2: frame + coordinate input + WS stream -----------
        print("\n=== Improvement 2: live viewport ===")
        r = client.get(f"/browser/sessions/{sid}/frame", params={"quality": 80})
        frame = r.json().get("frame_base64", "") if r.status_code == 200 else ""
        raw = base64.b64decode(frame) if frame else b""
        check("2./frame jpeg base64", r.status_code == 200 and raw[:3] == b"\xff\xd8\xff", f"{r.status_code} bytes={len(raw)}")

        r = client.post(f"/browser/sessions/{sid}/input", json={"type": "move", "x": 60, "y": 40})
        check("2./input move", r.status_code == 200 and r.json().get("ok") is True, r.text[:160])
        r = client.post(f"/browser/sessions/{sid}/input", json={"type": "click", "x": 60, "y": 40})
        check("2./input click", r.status_code == 200 and r.json().get("ok") is True, r.text[:160])
        time.sleep(0.4)
        t = client.get(f"/browser/sessions/{sid}/snapshot").json().get("text", "")
        check("2.coordinate click hit #probe", "PROBED" in t, t.replace("\n", " ")[:140])

        r = client.post(f"/browser/sessions/{sid}/input", json={"type": "scroll", "delta_y": -120})
        check("2./input scroll", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
        r = client.post(f"/browser/sessions/{sid}/input", json={"type": "keydown", "key": "Enter"})
        check("2./input keydown", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
        r = client.post(f"/browser/sessions/{sid}/input", json={"type": "bogus"})
        check("2./input rejects unknown type", r.json().get("ok") is False, r.text[:120])

        with client.websocket_connect(f"/browser/sessions/{sid}/stream") as ws:
            first = ws.receive_json()
            check("2.WS stream frame", first.get("type") == "frame", str(first)[:120])
            wsraw = base64.b64decode(first.get("frame_base64", ""))
            check("2.WS frame is jpeg", wsraw[:3] == b"\xff\xd8\xff", f"bytes={len(wsraw)}")
            ws.send_json({"type": "click", "x": 60, "y": 40})
            second = ws.receive_json()
            check("2.WS accepts client input", second.get("type") == "frame", str(second)[:120])

        # ---- Improve 6: SSE telemetry ----------------------------------
        print("\n=== Improvement 6: SSE event bus ===")
        # TestClient only returns an SSE body once the generator ends, so the
        # transport (frames over the wire, nav/audit/error events) is verified
        # against a real uvicorn server in /tmp/greenwich_verify/verify_sse.py.
        # Here we assert the bus itself: subscribe -> emit -> receive.
        from backend.engine_runtime import manager as _mgr

        async def _bus_roundtrip():
            q = _mgr.session_subscribe(sid)
            try:
                await _mgr.emit_session_event(sid, "audit:step",
                                              {"agent": "security", "finding": "demo", "severity": "medium"})
                return await asyncio.wait_for(q.get(), timeout=2)
            finally:
                _mgr.session_unsubscribe(sid, q)

        ev = asyncio.run(_bus_roundtrip())
        check("6.event bus audit:step", ev.get("event") == "audit:step" and ev["data"]["agent"] == "security", ev)
        nav_events = [s.action for s in _mgr_session_steps(sid) if s.action == "navigate"]
        check("6.nav receipt recorded", bool(nav_events), nav_events)

        # ---- Improve 1: shared browser + reaper -------------------------
        print("\n=== Improvement 1: multiplexing + reaper ===")
        r2 = client.post("/browser/sessions", json={
            "allow_domains": ["127.0.0.1"], "require_approval": False,
            "allow_private": True, "scrub_pii": False})
        sid2 = r2.json()["session_id"]
        check("1.second session opened", r2.status_code == 200, r2.text[:160])
        client.post(f"/browser/sessions/{sid2}/navigate", json={"url": file_url})
        bm = BrowserManager.get_instance()
        s1, s2 = bm._sessions[sid], bm._sessions[sid2]
        check("1.one Chromium shared", s1._browser is s2._browser, f"same={s1._browser is s2._browser}")
        check("1.no per-session process tree", (not s1._owns_browser) and (not s2._owns_browser))
        check("1.headless gpu flags", "--disable-gpu-compositing" in __import__("backend.modules.browser", fromlist=["x"])._chromium_headless_args())

        d = client.delete(f"/browser/sessions/{sid2}")
        check("1.delete closes only that context", d.status_code == 200, d.text[:160])
        r = client.post(f"/browser/sessions/{sid}/navigate", json={"url": file_url})
        check("1.sibling session survives", r.status_code == 200 and s1._browser.is_connected(), r.text[:120])

        # reaper: idle service evicts and stops the browser session
        from backend.modules.browser_agent_service import BrowserAgentService

        class FakeAgent:
            closed = False
            def __init__(self):
                self.created_at = time.time() - 600
                self.last_activity = time.time() - 600

        class FakeBS:
            stopped = False
            async def stop(self):
                FakeBS.stopped = True

        svc = BrowserAgentService(max_sessions=4, idle_ttl_seconds=1)
        svc._sessions["ghost"] = FakeAgent()
        svc._browser_sessions["ghost"] = FakeBS()
        check("1.idle TTL configurable", BrowserAgentService(max_sessions=4, idle_ttl_seconds=1).idle_ttl_seconds == 1)

        async def drain():
            svc._prune()
            await asyncio.sleep(0.15)
        asyncio.run(drain())
        check("1.reaper evicts idle + stops context", "ghost" not in svc._sessions and FakeBS.stopped)

        improvement5(client, sid)
        client.delete(f"/browser/sessions/{sid}")

    # after lifespan shutdown
    check("1.shutdown closed shared Chromium", shared_browser_info()["shared"] is False)


def main():
    improvement4()
    browser_improvements()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n=== SUMMARY: {len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed ===")
    for name, ok, detail in fails:
        print("FAILED:", name, "|", detail[:300])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
