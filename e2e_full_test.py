#!/usr/bin/env python3
"""
Jambubrowser Sovereign Elite v2.0 — Full E2E Test Suite
Tests all 89 API endpoints across all 4 phases.
"""
import httpx
import json
import time
import sys
import os

BASE = "http://localhost:8001"
PASS = 0
FAIL = 0
ERRORS = []

client = httpx.Client(base_url=BASE, timeout=30.0)


def test(method, path, data=None, expect=200, label=None):
    global PASS, FAIL
    url = path
    try:
        if method == "GET":
            r = client.get(url)
        elif method == "POST":
            r = client.post(url, json=data)
        elif method == "DELETE":
            r = client.delete(url)
        else:
            r = client.request(method, url, json=data)

        ok = r.status_code == expect
        status = "✅" if ok else "❌"
        if ok:
            PASS += 1
        else:
            FAIL += 1
            ERRORS.append(f"{method} {path} → {r.status_code} (expected {expect})")
        print(f"  {status} {method:6s} {path:45s} → {r.status_code}")
        return r
    except Exception as e:
        FAIL += 1
        ERRORS.append(f"{method} {path} → ERROR: {e}")
        print(f"  ❌ {method:6s} {path:45s} → ERROR: {e}")
        return None


def main():
    global PASS, FAIL

    print("=" * 80)
    print("  JAMBUBROWSER SOVEREIGN ELITE v2.0 — FULL E2E TEST SUITE")
    print("=" * 80)

    # ── Phase 0: Utility ──────────────────────────────────────
    print("\n── Phase 0: Utility & Research ──")
    test("GET", "/health")
    test("GET", "/version")
    test("GET", "/brain/documents")
    test("GET", "/brain/stats")
    test("GET", "/history")
    test("POST", "/research", {"query": "what is jambudweep"}, 200)
    test("DELETE", "/memory")

    # ── Phase 1: Transactional Autonomy ───────────────────────
    print("\n── Phase 1: Credential Vault ──")
    test("GET", "/vault/status")
    test("POST", "/vault/store", {"name": "github_token", "value": "ghp_test123", "credential_type": "token"})
    test("POST", "/vault/store", {"name": "ssh_key", "value": "-----BEGIN RSA-----\ntest\n-----END RSA-----", "credential_type": "ssh_key"})
    test("POST", "/vault/retrieve", {"name": "github_token"})
    test("GET", "/vault/list")
    test("DELETE", "/vault/remove/ssh_key", expect=200)
    test("DELETE", "/vault/remove/nonexistent", expect=404)

    print("\n── Phase 1: Action Engine ──")
    test("POST", "/action/preview", {
        "description": "Navigate to example.com",
        "steps": [{"type": "navigate", "url": "https://example.com"}, {"type": "click", "selector": "h1"}]
    })
    test("POST", "/action/execute", {
        "description": "Dry run test",
        "steps": [{"type": "navigate", "url": "https://example.com"}],
        "dry_run": True
    })
    test("GET", "/action/history")

    print("\n── Phase 1: Local Connector ──")
    test("GET", "/connector/obsidian/vaults")
    test("POST", "/connector/obsidian/append", {"note_path": "test/e2e.md", "content": "# E2E Test\nPhase 1 complete."})
    test("POST", "/connector/task/create", {"title": "E2E test task", "priority": "high"})
    test("GET", "/connector/task/list")
    test("POST", "/connector/file/write", {"path": "/tmp/test_e2e.txt", "content": "Hello from Jambubrowser!"})
    test("POST", "/connector/file/read", {"path": "/tmp/test_e2e.txt"})
    test("GET", "/connector/file/list?path=/tmp")

    print("\n── Phase 1: Skill Forge ──")
    test("POST", "/forge/test", {"code": "def execute(p): return {'sum': sum(range(10))}", "params": {}})
    test("POST", "/forge/deploy", {"name": "adder", "description": "Adds numbers", "code": "def execute(p): return {'result': p.get('a',0) + p.get('b',0)}"})
    test("GET", "/forge/list")
    test("POST", "/forge/execute/adder", {"a": 7, "b": 3})
    test("DELETE", "/forge/remove/adder")

    # ── Phase 2: Visual Grounding ─────────────────────────────
    print("\n── Phase 2: Visual Grounding ──")
    test("POST", "/visual/analyze", {"image_path": "/nonexistent.png"})
    test("POST", "/visual/extract-text", {"image_path": "/nonexistent.png", "region": {"x": 0, "y": 0, "width": 100, "height": 100}})
    test("POST", "/visual/identify-elements", {"image_path": "/nonexistent.png"})
    test("POST", "/visual/diff", {"before_path": "/a.png", "after_path": "/b.png"})

    # ── Phase 2: YouTube Intelligence ─────────────────────────
    print("\n── Phase 2: YouTube Intelligence ──")
    test("POST", "/youtube/metadata", {"video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    test("POST", "/youtube/transcript", {"video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    test("POST", "/youtube/search-channel", {"channel_url": "https://youtube.com/@3blue1brown", "query": "linear algebra"})
    test("POST", "/youtube/summarize", {"text": "First point about quantum computing. Second point about entanglement. Third point about superposition. Fourth point about decoherence. Fifth point about error correction.", "max_points": 3})
    test("POST", "/youtube/related", {"video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})

    # ── Phase 2: Multi-Modal Command ──────────────────────────
    print("\n── Phase 2: Multi-Modal Command ──")
    test("POST", "/command/parse", {"text": "search for quantum computing papers"})
    test("POST", "/command/parse", {"text": "go to https://arxiv.org and download the latest ML paper"})
    test("POST", "/command/autocomplete", {"partial": "sea"})
    test("POST", "/command/context")
    test("POST", "/command/voice", {"audio_path": "/nonexistent.wav"})
    test("POST", "/command/image", {"image_path": "/nonexistent.png", "question": "What is this?"})

    # ── Phase 3: Persistent Missions ──────────────────────────
    print("\n── Phase 3: Persistent Missions ──")
    test("POST", "/missions/create", {"query": "Monitor AI news daily", "schedule": "daily", "max_runs": 7})
    test("GET", "/missions/list")
    test("GET", "/missions/due")
    # Get the mission ID from the list
    r = client.get("/missions/list")
    if r and r.status_code == 200:
        missions = r.json().get("missions", [])
        if missions:
            mid = missions[0].get("id", "")
            if mid:
                test("GET", f"/missions/get/{mid}")
                test("POST", f"/missions/pause/{mid}")
                test("POST", f"/missions/resume/{mid}")
                test("POST", "/missions/complete", {"mission_id": mid, "results": {"articles": 5}})
                test("GET", f"/missions/history/{mid}")
                test("DELETE", f"/missions/delete/{mid}")

    # ── Phase 3: Shadow Browser ───────────────────────────────
    print("\n── Phase 3: Shadow Browser ──")
    test("POST", "/shadow/start", {"url": "https://example.com", "check_interval": 60})
    test("GET", "/shadow/list")
    test("POST", "/shadow/check", {"url": "https://example.com"})
    test("POST", "/shadow/changelog", {"url": "https://example.com"})
    test("POST", "/shadow/rules", {"url": "https://example.com", "rules": {"text_changes": True, "new_links": True}})
    test("POST", "/shadow/stop", {"url": "https://example.com"})

    # ── Phase 3: Risk Shield ──────────────────────────────────
    print("\n── Phase 3: Risk Shield ──")
    test("POST", "/risk/scan-url", {"url": "https://example.com"})
    test("POST", "/risk/scan-url", {"url": "http://192.168.1.1/login.php?id=drop+table"})
    test("POST", "/risk/scan-page", {"html": "<html><script src='https://coinhive.com/lib/coinhive.min.js'></script><body>test</body></html>", "url": "https://evil.com"})
    test("POST", "/risk/check-download", {"url": "https://evil.com/malware.exe", "filename": "malware.exe"})
    test("POST", "/risk/check-download", {"url": "https://safe.com/paper.pdf", "filename": "paper.pdf"})
    test("GET", "/risk/privacy-report")
    test("GET", "/risk/history")
    test("POST", "/risk/block", {"url": "https://evil.com", "reason": "phishing detected"})
    test("POST", "/risk/check-blocked", {"url": "https://evil.com"})
    test("POST", "/risk/unblock", {"url": "https://evil.com"})

    # ── Phase 4: Federated RAG ────────────────────────────────
    print("\n── Phase 4: Federated RAG ──")
    test("POST", "/federation/register", {"node_id": "node_alpha", "node_url": "http://192.168.1.10:8001", "public_key": "abc123"})
    test("POST", "/federation/register", {"node_id": "node_beta", "node_url": "http://192.168.1.11:8001"})
    test("GET", "/federation/nodes")
    test("POST", "/federation/share", {"node_id": "node_alpha", "documents": [{"id": "doc1", "text": "Quantum computing basics", "metadata": {"source": "test"}}]})
    test("POST", "/federation/query", {"query": "quantum", "max_nodes": 5})
    test("GET", "/federation/sync-status")
    test("DELETE", "/federation/unregister/node_beta")

    # ── Phase 4: Consensus Engine ─────────────────────────────
    print("\n── Phase 4: Consensus Engine ──")
    r = test("POST", "/consensus/propose", {"title": "Use Python or Rust?", "description": "Choose backend language", "options": ["Python", "Rust", "Go"]})
    if r and r.status_code == 200:
        pid = r.json().get("proposal_id", "")
        if pid:
            test("POST", "/consensus/vote", {"proposal_id": pid, "node_id": "node_alpha", "choice": "Python", "confidence": 0.9, "reasoning": "Faster prototyping"})
            test("POST", "/consensus/vote", {"proposal_id": pid, "node_id": "node_beta", "choice": "Rust", "confidence": 0.8, "reasoning": "Better performance"})
            test("GET", f"/consensus/proposal/{pid}")
            test("GET", f"/consensus/tally/{pid}")
            test("GET", f"/consensus/check/{pid}")
    test("GET", "/consensus/list")

    # ── Phase 4: Forensic Personas ────────────────────────────
    print("\n── Phase 4: Forensic Personas ──")
    r = test("POST", "/personas/create", {"name": "Researcher", "description": "Academic browsing persona", "block_trackers": True, "language": "en-US"})
    pid = None
    if r and r.status_code == 200:
        pid = r.json().get("persona_id", "")
    r2 = test("POST", "/personas/create", {"name": "Shopper", "description": "Shopping persona", "cookies_enabled": True})
    pid2 = None
    if r2 and r2.status_code == 200:
        pid2 = r2.json().get("persona_id", "")

    test("GET", "/personas/list")
    if pid:
        test("GET", f"/personas/get/{pid}")
        test("POST", f"/personas/activate/{pid}")
        test("GET", "/personas/active")
        test("POST", "/personas/activity", {"persona_id": pid, "activity_type": "visit", "details": {"url": "https://arxiv.org"}})
        test("POST", "/personas/activity", {"persona_id": pid, "activity_type": "search", "details": {"query": "quantum computing"}})
        test("GET", f"/personas/activity/{pid}")
        test("GET", f"/personas/stats/{pid}")
        test("POST", f"/personas/export/{pid}")

    test("POST", "/personas/deactivate")
    if pid2:
        test("DELETE", f"/personas/delete/{pid2}")
    if pid:
        test("DELETE", f"/personas/delete/{pid}")

    # ── Summary ───────────────────────────────────────────────
    total = PASS + FAIL
    print("\n" + "=" * 80)
    print(f"  RESULTS: {PASS}/{total} passed ({PASS*100//total}%) — {FAIL} failures")
    print("=" * 80)
    if ERRORS:
        print("\n  FAILURES:")
        for e in ERRORS:
            print(f"    ❌ {e}")
    print()
    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
