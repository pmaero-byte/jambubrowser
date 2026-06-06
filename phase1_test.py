#!/usr/bin/env python3
"""Phase 1 E2E Test — Harness Gateway Compatibility
Tests all /v1/* endpoints added to Jambubrowser.
"""
import httpx, json, sys

BASE = "http://localhost:8001"
PASS = FAIL = 0

def test(method, path, data=None, params=None, expected_status=200):
    global PASS, FAIL
    try:
        c = httpx.Client(base_url=BASE, timeout=30)
        if method == "GET":
            r = c.get(path, params=params)
        elif method == "POST":
            r = c.post(path, json=data)
        elif method == "DELETE":
            r = c.delete(path)
        else:
            print(f" ❌ Unknown method: {method}")
            FAIL += 1
            return

        if r.status_code == expected_status:
            PASS += 1
            print(f"  ✅ {method} {path} → {r.status_code}")
        else:
            FAIL += 1
            print(f"  ❌ {method} {path} → {r.status_code} (expected {expected_status})")
            print(f"     Body: {r.text[:200]}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {method} {path} → ERR: {e}")

print("=" * 60)
print("PHASE 1 — HARNESS GATEWAY COMPATIBILITY E2E TESTS")
print("=" * 60)

# ── Health ──
print("\n── Health ──")
test("GET", "/health")
test("GET", "/v1/health/detailed")

# ── /v1/run ──
print("\n── /v1/run ──")
test("POST", "/v1/run", {"prompt": "search for AI coding tools"})
test("POST", "/v1/run", {"prompt": "remember how to auth"})
test("POST", "/v1/run", {"prompt": "research quantum computing"})
test("POST", "/v1/run", {"prompt": "hello world python"})
test("POST", "/v1/run", {"prompt": "scrape https://example.com"}, expected_status=200)

# ── /v1/run/stream ──
print("\n── /v1/run/stream ──")
try:
    c = httpx.Client(base_url=BASE, timeout=30)
    with c.stream("POST", "/v1/run/stream", json={"prompt": "hello"}) as r:
        chunks = []
        for line in r.iter_lines():
            if line.startswith("data: "):
                chunks.append(line[6:])
        if chunks:
            PASS += 1
            print(f"  ✅ POST /v1/run/stream → SSE ({len(chunks)} events)")
        else:
            FAIL += 1
            print(f"  ❌ POST /v1/run/stream → No SSE events")
except Exception as e:
    FAIL += 1
    print(f"  ❌ POST /v1/run/stream → ERR: {e}")

# ── /v1/memory ──
print("\n── /v1/memory ──")
test("POST", "/v1/memory", {"category": "test", "key": "hello", "value": "world", "importance": 0.8})
test("POST", "/v1/memory", {"category": "test", "key": "auth", "value": "Use JWT with RS256", "importance": 0.9})
test("POST", "/v1/memory", {"category": "architecture", "key": "db", "value": "PostgreSQL for production"})
test("GET", "/v1/memory")
test("GET", "/v1/memory", params={"category": "test", "limit": 10})
test("POST", "/v1/memory/search", {"query": "auth", "limit": 5})
test("POST", "/v1/memory/search", {"query": "hello"})
test("DELETE", "/v1/memory/1", expected_status=200)

# ── /v1/sessions ──
print("\n── /v1/sessions ──")
test("GET", "/v1/sessions")
test("GET", "/v1/sessions", params={"limit": 5})

# ── /v1/models ──
print("\n── /v1/models ──")
test("GET", "/v1/models")

# ── /v1/connectors ──
print("\n── /v1/connectors ──")
test("GET", "/v1/connectors")

# ── /analytics/summary ──
print("\n── /analytics/summary ──")
test("GET", "/analytics/summary")
test("GET", "/analytics/summary", params={"days": 3})

# ── Stats ──
print("\n" + "=" * 60)
total = PASS + FAIL
pct = PASS / total * 100 if total > 0 else 0
print(f"RESULTS: {PASS}/{total} passed ({pct:.0f}%)")
if FAIL > 0:
    print(f"FAILURES: {FAIL}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED ✅")
    sys.exit(0)
