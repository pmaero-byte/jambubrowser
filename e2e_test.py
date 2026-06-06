#!/usr/bin/env python3
"""Jambubrowser v2.0 — E2E Test (jambubrowser repo, 97 routes)"""
import httpx, json, sys
BASE = "http://localhost:8001"; PASS = FAIL = 0

def test(method, path, data=None):
    global PASS, FAIL
    url = f"{BASE}{path}"
    try:
        if method == "GET": r = httpx.get(url, timeout=10)
        elif method == "POST": r = httpx.post(url, json=data, timeout=30)
        elif method == "DELETE": r = httpx.delete(url, timeout=10)
        elif method == "PUT": r = httpx.put(url, json=data, timeout=10)
        else: raise ValueError(method)
        ok = r.status_code in (200, 201)
    except: ok = False
    if ok: PASS += 1
    else: FAIL += 1
    sc = r.status_code if 'r' in dir() else 'ERR'
    print(f"  {'✅' if ok else '❌'} {method:6s} {path[:65]} → {sc}")
    return r if 'r' in dir() else None

print("="*80 + "\n  JAMBUBROWSER v2.0 — E2E TEST (jambubrowser repo)\n" + "="*80)

print("\n── Utility ──")
test("GET", "/health"); test("GET", "/stats")

print("\n── Research ──")
test("POST", "/research", {"query": "quantum computing"})
test("GET", "/search?q=quantum+computing&max_results=3")
test("POST", "/scrape", {"url": "https://example.com"})

print("\n── Execution ──")
test("POST", "/exec", {"code": "print('hello')"})

print("\n── Browser Automation ──")
test("POST", "/act", {"url": "https://example.com", "steps": []})
test("POST", "/workflow/execute", {"url": "https://example.com", "steps": []})

print("\n── Credential Vault ──")
test("POST", "/login", {"username": "user", "password": "pass", "url": "https://example.com"})
test("GET", "/vault/credential?url=https://github.com/login")
test("GET", "/vault/domains")

print("\n── Vision ──")
test("POST", "/vision/grounding", {"url": "https://example.com/img.png", "query": "describe"})
test("POST", "/vision/analyze", {"url": "https://example.com/img.png", "query": "analyze"})

print("\n── Memory ──")
test("GET", "/memory/recall?query=test&limit=5")
test("GET", "/graph_data")

print("\n── Missions ──")
test("POST", "/mission", {"name": "Test Mission", "query": "test"})
test("POST", "/mission/schedule", {"name": "Test", "cron": "0 9 * * *", "query": "test"})
test("GET", "/mission/list")
test("POST", "/mission/start-scheduler", {})
test("POST", "/mission/stop-scheduler", {})

print("\n── Tools / Skill Forge ──")
test("POST", "/tool/save", {"name": "test_tool", "description": "test", "code": "def run(**kwargs): return {'ok': True}"})
test("GET", "/tools")
test("POST", "/tool/exec", {"name": "test_tool", "kwargs": {}})
test("GET", "/skill/list-synthesized")

print("\n── Forms ──")
test("POST", "/forms/detect", {"html": "<form></form>", "url": "https://example.com"})
test("POST", "/forms/fill-script", {"html": "<form></form>", "fields": {}, "url": "https://example.com"})

print("\n── Local Connector ──")
test("POST", "/local/obsidian/create", {"title": "E2E Test", "content": "test"})
test("POST", "/local/obsidian/append", {"title": "E2E Test", "content": "more"})
test("GET", "/local/obsidian/read?title=E2E+Test")
test("GET", "/local/obsidian/search?query=E2E")
test("GET", "/local/obsidian/stats")
test("POST", "/local/reminders/create", {"title": "Test", "when": "in 1 hour"})
test("POST", "/local/clipboard/copy?text=clipboard+test+content")
test("GET", "/local/clipboard/paste")
test("POST", "/local/notes/save", {"title": "test", "content": "hello"})

print("\n── Knowledge Graph ──")
test("POST", "/knowledge/ingest", {"text": "Quantum computing uses qubits", "metadata": {"source": "test"}})
test("GET", "/knowledge/graph")
test("GET", "/knowledge/search?query=quantum")
test("GET", "/knowledge/clusters")
test("GET", "/knowledge/stats")

print("\n── P2P / Federation ──")
test("GET", "/p2p/info")
test("POST", "/p2p/discover", {})
test("GET", "/p2p/peers")
test("POST", "/p2p/query", {"query": "test", "node_id": "local", "filters": {}})
test("POST", "/p2p/start-discovery", {})
test("GET", "/p2p/stats")
test("GET", "/peer/info")
test("POST", "/peer/query", {"query": "test"})
test("GET", "/federated/stats")

print("\n── Multimodal Input ──")
test("POST", "/multimodal/text", {"text": "search for papers"})
test("POST", "/multimodal/image", {"image_data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "filename": "1px.png"})
test("POST", "/multimodal/file", {"file_data": "aGVsbG8=", "filename": "test.txt"})

print("\n── YouTube Intelligence ──")
test("POST", "/media/youtube?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ&action=metadata", data={})
test("GET", "/media/youtube/transcript?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
test("GET", "/media/youtube/search?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ&query=never")

print("\n── Risk Shield ──")
test("POST", "/shield/check", {"url": "https://example.com"})
test("POST", "/shield/batch", {"urls": ["https://example.com"]})
test("GET", "/shield/stats")

print("\n── Shadow Browser ──")
test("POST", "/shadow/start", {})
test("GET", "/shadow/stats")
test("GET", "/shadow/interests")
test("POST", "/shadow/stop", {})

print("\n── Notifications ──")
test("GET", "/notifications/history")
test("POST", "/notifications/send?title=Test&message=E2E+notification")

print("\n── Fingerprint Rotator ──")
test("POST", "/fingerprint/generate", {})
test("GET", "/fingerprint/list")
test("POST", "/fingerprint/rotate", {})

print("\n── API Discovery ──")
test("POST", "/discover_api", {"url": "https://example.com", "endpoints": ["/health"]})
test("POST", "/api/call", {"url": "https://example.com", "endpoint": "/health", "method": "GET"})

print("\n── Consensus Engine (NEW) ──")
r = test("POST", "/consensus/propose", {"title": "Test Vote", "description": "Should we proceed?", "options": ["Yes", "No"], "required_nodes": 2})
pid = None
if r and r.status_code == 200:
    resp = r.json()
    pid = resp.get("proposal_id") or resp.get("proposal", {}).get("id", "")
if pid:
    test("POST", "/consensus/vote", {"proposal_id": pid, "node_id": "node1", "choice": "Yes", "confidence": 0.9, "reasoning": "Looks good"})
    test("POST", "/consensus/vote", {"proposal_id": pid, "node_id": "node2", "choice": "Yes", "confidence": 0.8})
    test("GET", f"/consensus/proposal/{pid}")
    test("GET", f"/consensus/tally/{pid}")
    test("GET", f"/consensus/check/{pid}")
    test("POST", f"/consensus/close/{pid}")
test("GET", "/consensus/list")

print("\n" + "="*80)
total = PASS + FAIL
pct = round(100*PASS/total) if total else 0
print(f"  RESULTS: {PASS}/{total} passed ({pct}%) — {FAIL} failures")
print("="*80)
sys.exit(0 if FAIL == 0 else 1)
