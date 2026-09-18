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

## Debugging capabilities (M1)

### Request interception / API mocking
Pass a `network` policy to run the app in any state (error, empty, slow, offline):

```json
{"network": {
  "mocks": [{"url": "**/api/user", "json": {"name": "Dev"}, "status": 200}],
  "fail":  ["**/analytics/**"],
  "delay": [{"url": "**/api/slow", "ms": 3000}],
  "offline": false
}}
```

Rules are first-match-wins. Requests are tracked, so
`assert_made_request{value}` / `assert_no_request{value}` verify the app called
(or didn't call) an endpoint.

### Cause attribution
Every step carries what it changed — no extra calls needed:

```json
{"i": 3, "action": "click", "status": "passed",
 "cause": {"console_errors": ["simulated app error"],
           "failed_requests": [{"method": "POST", "url": ".../api/order", "failure": "net::ERR"}],
           "dom": {"added": 1, "removed": 0, "changed": 2, "added_names": ["Error banner"]}}}
```

### Debug artifacts
`trace: true`, `har: true`, `video: true` capture Playwright trace / HAR / video;
paths are returned under `artifacts` and files persist after the run.

### Accessibility & performance budgets
```json
[{"action": "assert_no_a11y_violations"},
 {"action": "assert_lcp", "value": 2500},
 {"action": "assert_fcp", "value": 1800},
 {"action": "assert_load", "value": 3000},
 {"action": "assert_dom_nodes", "value": 1500},
 {"action": "assert_transfer_kb", "value": 500}]
```
The a11y probe is dependency-free (image alt, labels, button/link names,
`html lang`, document title, duplicate ids, positive tabindex, heading order).
Performance metrics come from Navigation Timing, Paint Timing and an injected
LCP/Layout-Shift observer. A `0` LCP means the observer saw no candidate.

### Source-map-aware errors
`resolve_sources: true` maps console errors through the page's source maps
(Base64-VLQ decoder built in) and returns `console_errors_source` with
`source` / `source_line`.

### Determinism
Animations and transitions are neutralised before each flow
(`freeze_animations: true`, default) to remove flake.

### Auth seeding
Pass `storage_state` (`{cookies, origins}`) to start already logged in; combine
with the credential vault to keep secrets out of the model context.

## Authoring, matrix, export & monitors (M2)

### Plan from a goal
`POST /browser/sessions/plan` (MCP: `browser_test_plan`, CLI: `jambu plan`)
turns plain English into steps via a template library (login, signup,
checkout, search, accessibility, performance, responsive, smoke). Optional
`use_llm=true` refines with the configured provider; the endpoint works with
no model at all.

### Responsive / locale matrix
`POST /browser/sessions/matrix` (MCP: `browser_test_matrix`) runs one flow
across viewports/locales concurrently (capped at the session limit) and
returns a per-variant digest. Variants set `name` plus `viewport`, `locale`,
`user_agent`, `device_scale_factor`, `timezone_id`, …

### Export to Playwright
`POST /browser/sessions/export` (MCP: `browser_export_playwright`,
CLI: `jambu export flow.json --out app.spec.ts`) renders a flow as
`.spec.ts` so teams can move it into their own CI.

### CLI
```bash
jambu plan "test login" --url http://localhost:3000
jambu test flow.json --local --trace --resolve-sources
jambu export flow.json --out login.spec.ts
```

### Flow monitors
`/browser/monitors` stores a flow and re-runs it on an interval, persisting
each run and alerting (desktop + webhook) on failure. A scheduler starts with
the engine. This turns a one-off debug session into permanent regression
protection.

### Semantic diff
`POST /browser/sessions/semantic-diff` produces a human-readable change
summary over element catalogs (added / removed / renamed / state changes),
optionally explaining it with the LLM (`explain: true`) and/or adding a vision
description over two screenshots (`use_vision: true`). Pixel diffing stays as
the deterministic fallback.

### One-call meta-tool
MCP `browser_task(url, goal, inputs)` plans a flow from a goal, substitutes
`{{placeholder}}` values from `inputs`, and runs it — one tool call.

### Developer MCP profile
`JAMBU_MCP_PROFILE=developer` exposes only the seven high-level browser-testing
verbs (`browser_task`, `browser_test_flow`, `browser_test_plan`,
`browser_test_matrix`, `browser_session_run`, `browser_export_playwright`,
`check_engine_health`), minimising tool-selection cost.

### Live view / human takeover (backend)
`GET /browser/sessions/{id}/screenshot` returns the current frame as base64;
`POST /browser/sessions/{id}/takeover {active}` pauses/resumes agent control
for CAPTCHA/2FA or visual checks. The desktop UI that consumes these is the
remaining (Rust/webview) milestone.

### Dev-server discovery & settle
`GET /browser/dev-servers` (CLI: `jambu dev-servers`) scans common ports and
identifies the framework (Vite/Next/CRA/Nuxt/Remix/SvelteKit/Astro/Angular/
webpack/Django). `GET /browser/dev-servers/probe?url=…` probes one URL.
`run_test(..., detect_dev_server=true)` attaches a `dev_server` block
(reachable, framework, title, server) for loopback targets, and `settle_ms=N`
waits until the resource count has been stable for N ms after each navigation
— the hot-reload settle that stops flows racing a rebuild.

### Recording a flow
Any client driving a session can record it into a reusable flow:
`POST /browser/sessions/{id}/record {active}` and
`GET /browser/sessions/{id}/flow`. Recorded credentials become replayable
placeholders (`{{email}}`, `{{password}}`) rather than being stored verbatim.
CLI: `jambu record --session <id> [--stop --out flow.json]`.

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
