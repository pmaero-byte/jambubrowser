# Token-efficient browser testing for AI agents

Jambubrowser can run a **complete product test in one tool call** against a
local dev server. An AI agent describes the whole flow declaratively; the
engine drives a real Playwright browser, resolves elements by intent,
auto-collects console/network telemetry, and returns one compact pass/fail
report. No snapshots and clicks round-tripped per step.

## Why this exists

The classic perception loop is correct but expensive:

```
open → navigate → snapshot → act → snapshot → act → snapshot → close
```

For a 10-step test that is 40–50 tool calls, most carrying a 200-element DOM
catalog. At ~1–3k tokens per snapshot, a single test can burn tens of
thousands of tokens before the model reasons about anything.

The flow runner collapses that to **one call**:

```
browser_test_flow(url, steps=[...])  →  PASS 9/9 + console/network digest
```

## The one-call API

### MCP tool (external agents: Claude, Cursor, …)

```
browser_test_flow(
  url="http://localhost:3000",
  steps='[
    {"action": "navigate", "url": "http://localhost:3000/login"},
    {"action": "type", "target": "Email", "value": "dev@example.com"},
    {"action": "type", "target": "Password", "value": "secret"},
    {"action": "click", "target": "Sign in"},
    {"action": "assert_visible", "target": "Dashboard"},
    {"action": "assert_console_clean"}
  ]',
  local=true
)
```

Set `local=true` so loopback/private hosts are permitted — the engine's SSRF
guard otherwise blocks `localhost` by design.

### HTTP

- `POST /browser/sessions/run` — one-shot: open → run → close.
- `POST /browser/sessions/{id}/run` — run a flow against an existing session.
- `POST /browser/sessions` now accepts `allow_private: true` for local dev.

### Internal ReAct agent

`browser_test_flow` is registered as a built-in tool
(`backend/agent/builtin_tools.py`), so the engine's own agent loop gets the
same single-call capability.

## Step schema

A flow is a JSON array of step objects. `target` is resolved against the
current element catalog by: exact `@eN` ref → exact name → `"role name"`
(e.g. `button Sign in`) → unique substring. Ambiguity returns bounded
candidates so the agent can retry without another snapshot call.

| Action | Fields | Notes |
|---|---|---|
| `navigate` | `url` | allowlist- and SSRF-checked |
| `click` | `target`/`ref` | risky elements need `approve=true` |
| `type` | `target`/`ref`, `value` | fills the field |
| `press` | `key`, optional `target`/`ref` | e.g. `Enter` |
| `hover` / `select` / `check` / `uncheck` | `target`/`ref`, `value` | |
| `reload` / `back` / `forward` | — | |
| `wait` | `selector` \| `text` \| `url_contains` | otherwise waits network idle |
| `screenshot` | `full_page?` | base64 returned (stripped from agent reports) |
| `assert_visible` | `target` | interactive element **or** rendered text |
| `assert_not_visible` | `target` | |
| `assert_text` / `assert_text_equals` | `target?`, `value` | page text if no target |
| `assert_value` | `target`, `value` | input value |
| `assert_url` / `assert_title` | `value` | substring match |
| `assert_count` | `target?`, `value` | catalog element count |
| `assert_checked` / `assert_unchecked` | `target` | |
| `assert_enabled` / `assert_disabled` | `target` | |
| `assert_console_clean` | — | no console/page errors so far |
| `assert_no_failed_requests` | — | no failed network requests |

Flow-level options: `approve` (approve risky/input actions for every step),
`stop_on_failure`, `observe` (internal re-observe; leave on).

## What comes back

A compact report — not the DOM:

```json
{
  "ok": false, "passed": 6, "failed": 1, "total": 7,
  "steps": [
    {"i": 4, "action": "assert_visible", "status": "failed",
     "reason": "assertion_failed", "error": "element/text missing: Save"}
  ],
  "console_errors": ["simulated app error for telemetry"],
  "failed_requests": [],
  "bad_responses": [{"status": 404, "method": "GET", "url": ".../missing.json"}],
  "final_url": "http://localhost:3000/",
  "title": "Jambu Local Test App",
  "duration_ms": 812
}
```

The MCP/agent renderers emit a token-lean Markdown digest; screenshots are
replaced by a `"captured"` marker.

## Local dev mode and safety

`local=true` is an **explicit opt-in** that sets `allow_private` for the
session, which relaxes `is_safe_url` only for loopback/private addresses —
and only for hosts that are *also* in the session allowlist. Both gates must
agree:

- Without `local=true`, `http://localhost:3000` is refused (`unsafe_url`).
- With `local=true`, `http://127.0.0.1:3000` still needs `127.0.0.1` in the
  allowlist (`blocked_domain` otherwise).
- `"*"` in an allowlist is deny-all, never allow-all.

Risky elements (`delete`, `pay`, `send`, `confirm`, …) always require
`approve=true`, even when local. Every step — including refusals — is written
to the hash-chained receipt log; the session's Merkle root is returned in the
report.

## Token accounting

| Scenario | Calls (before) | Calls (now) |
|---|---|---|
| Smoke test a page loads | 3–5 | 1 |
| Login + assert dashboard | ~12 | 1 |
| 10-step regression flow | 40–50 | 1 |

Telemetry that used to require separate console/network tool calls is attached
to the same response.

## Related

- `docs/BROWSER_SESSIONS.md` — the hardened session loop and its rails.
- `docs/MCP_TOOLS.md` — generated MCP tool reference (37 tools).
