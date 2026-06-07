#!/usr/bin/env python3
"""Final E2E Test — All Phases (1-6) of Harness Integration"""
import httpx, json, sys, time

BASE = "http://localhost:8001"
PASS = FAIL = 0

def test(method, path, expected_status=200, json_data=None, data=None):
    global PASS, FAIL
    try:
        c = httpx.Client(base_url=BASE, timeout=60)
        if method == "GET":
            r = c.get(path)
        elif method == "POST":
            r = c.post(path, json=json_data, data=data)
        elif method == "DELETE":
            r = c.delete(path)
        else:
            r = c.request(method, path)
        
        if r.status_code == expected_status:
            PASS += 1
            print(f"  ✅ {method} {path} → {r.status_code}")
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        else:
            FAIL += 1
            print(f"  ❌ {method} {path} → {r.status_code} (expected {expected_status})")
            return None
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {method} {path} → ERROR: {e}")
        return None

print("=" * 60)
print("FINAL E2E TEST — ALL PHASES (1-6)")
print("=" * 60)

# Phase 1: Health
print("\n── Phase 1: Health ──")
test("GET", "/health")
test("GET", "/v1/health/detailed")

# Phase 1: /v1/run
print("\n── Phase 1: /v1/run ──")
test("POST", "/v1/run", json_data={"prompt": "search for python tutorials"})
test("POST", "/v1/run", json_data={"prompt": "remember my name is Prabaharan"})
test("POST", "/v1/run", json_data={"prompt": "research machine learning"})
test("POST", "/v1/run", json_data={"prompt": "scrape https://example.com"})
test("POST", "/v1/run", json_data={"prompt": "hello world"})

# Phase 1: /v1/run/stream
print("\n── Phase 1: /v1/run/stream ──")
try:
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/v1/run/stream", json={"prompt": "search for AI"}, headers={"Accept": "text/event-stream"})
    events = [line for line in r.text.split("\n") if line.startswith("data:")]
    if len(events) >= 3:
        PASS += 1
        print(f"  ✅ POST /v1/run/stream → SSE ({len(events)} events)")
    else:
        FAIL += 1
        print(f"  ❌ POST /v1/run/stream → SSE ({len(events)} events, expected >=3)")
except Exception as e:
    FAIL += 1
    print(f"  ❌ POST /v1/run/stream → ERROR: {e}")

# Phase 1: Memory
print("\n── Phase 1: Memory ──")
test("POST", "/v1/memory", json_data={"category": "test", "key": "name", "value": "Prabaharan", "importance": 0.9})
test("POST", "/v1/memory", json_data={"category": "test", "key": "project", "value": "Jambubrowser", "importance": 0.8})
test("POST", "/v1/memory", json_data={"category": "test", "key": "role", "value": "AI Engineer", "importance": 0.7})
test("GET", "/v1/memory")
test("GET", "/v1/memory?category=test")
test("POST", "/v1/memory/search", json_data={"query": "Prabaharan"})
test("POST", "/v1/memory/search", json_data={"query": "Jambubrowser"})

# Phase 1: Sessions
print("\n── Phase 1: Sessions ──")
test("GET", "/v1/sessions")
test("GET", "/v1/sessions?limit=5")

# Phase 1: Models & Connectors
print("\n── Phase 1: Models & Connectors ──")
test("GET", "/v1/models")
test("GET", "/v1/connectors")

# Phase 1: Analytics
print("\n── Phase 1: Analytics ──")
test("GET", "/analytics/summary")
test("GET", "/analytics/summary?days=30")

# Phase 2: Computer Use
print("\n── Phase 2: Computer Use ──")
test("GET", "/computer/capture")
test("GET", "/computer/capture?region=full")
test("POST", "/computer/mouse?action=click&x=100&y=200")
test("POST", "/computer/keyboard?text=hello")
test("POST", "/computer/keyboard?key=return")
test("POST", "/computer/launch?app_name=Calculator")
test("GET", "/computer/apps")

# Phase 3: Vision Engine
print("\n── Phase 3: Vision Engine ──")
# These need actual image data, test with empty/invalid
test("POST", "/vision/ocr", json_data={"image_data": "iVBORw0KGgo="})
test("POST", "/vision/ui-elements", json_data={"image_data": "iVBORw0KGgo="})
test("POST", "/vision/verify", json_data={"image_data": "iVBORw0KGgo=", "expected": "a login screen"})

# Phase 5: Plugin System
print("\n── Phase 5: Plugin System ──")
test("GET", "/plugins/list")
test("GET", "/plugins/web_search")
test("GET", "/plugins/memory")
test("POST", "/plugins/execute", json_data={"plugin_name": "memory", "params": {"action": "list"}})
test("POST", "/plugins/chain", json_data={"steps": [
    {"plugin": "memory", "params": {"action": "search", "query": "test"}}
]})

# Harness Bridge (from remote)
print("\n── Harness Bridge ──")
test("GET", "/harness/status")
test("POST", "/harness/context/store?key=test_key&value=test_value")
test("POST", "/harness/context/search?query=test")

# Summary
print("\n" + "=" * 60)
total = PASS + FAIL
pct = (PASS / total * 100) if total > 0 else 0
print(f"RESULTS: {PASS}/{total} passed ({pct:.0f}%)")
if FAIL == 0:
    print("ALL TESTS PASSED ✅")
else:
    print(f"FAILURES: {FAIL}")
print("=" * 60)
