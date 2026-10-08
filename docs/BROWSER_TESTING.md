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

**Selectors:** any action/assertion also accepts `selector` (CSS or
`xpath=…`/`//…`), dispatched directly against the page for elements the
catalog never sees — this is what imported Playwright specs use. Selector
dispatch always requires `approve=true` (unclassified elements are treated
as risky); the post-action allowlist check still applies.

**Evaluate:** `{"action": "evaluate", "script": "…"}` runs JS in the page and
returns the (PII-scrubbed, truncated) result. Requires `approve=true`. Scrubbing
is **off by default for local targets** (loopback / RFC1918 / `.local`) and on
for public hosts, so an application whose output is numbers can assert on them
directly; `scrub_pii` on `/run` or `/browser/sessions` forces either way, and a
per-step `scrub` overrides the session. The report states which policy ran.

| Action | Fields | Notes |
|---|---|---|
| `navigate` | `url` | allowlist- and SSRF-checked |
| `click` | `target`/`ref` | risky elements need `approve=true` |
| `type` | `target`/`ref`, `value` | fills the field |
| `press` | `key`, optional `target`/`ref` | e.g. `Enter` |
| `hover` / `select` / `check` / `uncheck` | `target`/`ref`, `value` | |
| `reload` / `back` / `forward` | — | |
| `wait` | `selector` \| `text` \| `url_contains` \| `js` \| `network_idle` | `js` is a page predicate (`approve=true`). `selector`/`text` wait for **rendered** content by default (`visible: false` opts into existence). With no condition it settles on network quiet |
| `dialog` | `dialog` | answers the dialog raised by the **next** action — see below |
| `upload` | `target`/`ref`/`selector`, `files`, `approve: true`, `chooser?` | file input vs. the OS picker |
| `download` | `target`/`ref`/`selector`, `match?`, `min_bytes?`, `sha256?`, `contains?` | saved under `JAMBU_DOWNLOAD_DIR`; content bounds are enforced |
| `assert_download` / `expect_download` | `min_bytes?`, `sha256?`, `contains?`, `path?` | verifies the previous download; the `path` is returned for a follow-up step |
| `drag` | `from`/`to` **or** `selector` + `to`, `steps?` (20), `button?` | pointer movement; `to` may be `{x,y}` or `{dx,dy}` |
| `wheel` | `x`, `y`, `dx?`, `dy?` | position required — zoom-to-cursor reads it |
| `mouse` | `event: down\|move\|up`, `x`/`y` or `selector`, `button?` | a gesture that straddles steps (press-and-hold) |
| `click_at` | `x`, `y`, `button?` | coordinate click |
| `dblclick` | `target`/`selector` **or** `x`/`y` | |
| `set_range` | `selector`, `value` | `<input type=range>`; sets value, fires `input`+`change`, then drags the handle |
| `screenshot` | `full_page?` | base64 returned (stripped from agent reports) |
| `evaluate` | `script`, `approve: true`, `scrub?` | run JS in the page; `scrub` overrides the session policy for this step |
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
| `assert_dialog` | `type?`, `value?`, `accepted?` | last dialog raised (`alert`/`confirm`/`prompt`/`beforeunload`) |
| `assert_no_dialog` | — | nothing raised a dialog so far |
| `assert_canvas` | `selector`, `min_non_background_pct?`, `min_colors?`, `max_colors?`, `min_brightness?`, `max_brightness?` | asserts the rendered frame; needs ≥1 bound |
| `assert_screenshot` | `name`, `threshold?` (0.5%), `masks?`, `selector?` | visual regression vs. a stored baseline |
| `assert_not_screenshot` | `name`, `threshold?` | the negative case |
| `assert_request_body` | `url`, `body_path?` / `body_contains?` / `body_equals?`, `method?` | what the page actually sent |
| `assert_request_status` | `url`, `value` | the response status |
| `assert_request_fast` | `url`, `max_ms?` (500) | per-request timing budget |
| `assert_no_warnings` | — | no console warnings so far |

**Narrowing a target.** Every `assert_*` also accepts `nth` (1-based; negative
counts from the end) and `within` (a scope selector), so an ambiguous label is
resolvable: `{"action":"assert_visible","selector":"button","text":"Skip","nth":2}`.
`testid`, `role` + `name` and `xpath` are accepted as targets directly. The
narrowing is recorded in the step detail, so a pass still says which element it
looked at.

**Timeouts.** `timeout` (ms) is honoured on every action, not just `wait` — it is
clamped to `1…120000`, and `0` means "as soon as possible".

**Pointer gestures** require `approve=true`: a gesture lands wherever the pointer
is, and the risk classifier cannot see through it. A drag defaults to 20
intermediate points — a single jump is not a drag, and orbit controls read the
movement stream.

**Dialogs, files and JS waits.** A native dialog is answered by *staging* the
answer before the action that raises it, because the listener has to exist when
the page calls `alert()`. Either send a `dialog` step first, or put the same
spec on the acting step:

```json
[
  {"action": "dialog", "dialog": "accept:blue"},
  {"action": "click", "target": "Rename", "approve": true},
  {"action": "click", "target": "Delete account", "dialog": "dismiss"},
  {"action": "assert_dialog", "type": "confirm", "value": "Delete", "accepted": false},
  {"action": "upload", "target": "Avatar upload", "files": ["tmp/avatar.png"],
   "approve": true},
  {"action": "upload", "target": "Attach file", "files": ["tmp/a.csv"],
   "chooser": true, "approve": true},
  {"action": "download", "target": "Export CSV", "match": "*.csv"},
  {"action": "wait", "js": "window.ready === true", "approve": true}
]
```

`dialog` accepts `"accept"`, `"dismiss"`, `"accept:<text>"` for `prompt()`, or
`{"accept": false, "text": "…"}`; the policy is one-shot and every dialog — even
one nobody asked about — lands in telemetry (`browser_session_telemetry`, and
the `dialog` diagnostics in the report). Uploads read files from the engine's
own disk, so they need `approve=true` **and** a path inside `JAMBU_UPLOAD_ROOTS`
(default: the working directory); `chooser: true` is for a button that opens the
OS picker instead of a visible `<input type=file>`. Downloads are saved under
`JAMBU_DOWNLOAD_DIR` and a `match` glob that the filename fails is a step
failure (`download_mismatch`), not a silent save. A download that produced no
file at all is `download_empty`, and a file that fails `min_bytes` / `sha256` /
`contains` is `download_failed` — a name match alone is not proof that an export
worked. The saved `path` is returned so a later step can parse it.

Flow-level options: `approve` (approve risky/input actions for every step),
`stop_on_failure`, `observe` (internal re-observe; leave on), `network_idle`
(wait for requests to go quiet after each passing step — for a dev server still
streaming lazy chunks).

## What comes back

A compact report — not the DOM:

```json
{
  "ok": false, "passed": 6, "failed": 1, "total": 7,
  "steps": [
    {"i": 4, "action": "assert_visible", "status": "failed",
     "reason": "assertion_failed", "error": "element/text missing: Save"}
  ],
  "evaluated": [{"i": 2, "action": "evaluate", "value": "max displacement 0.1234"}],
  "screenshots": [{"i": 7, "action": "screenshot", "bytes": 41203}],
  "console_errors": ["simulated app error for telemetry"],
  "failed_requests": [],
  "bad_responses": [{"status": 404, "method": "GET", "url": ".../missing.json"}],
  "final_url": "http://localhost:3000/",
  "title": "Jambu Local Test App",
  "scrub_pii": false,
  "duration_ms": 812
}
```

`evaluated` carries the value every `evaluate` step read and `screenshots`
indexes the captures, so the numbers a step produced and the evidence for a
visual pass travel with the report instead of having to be re-fetched.
`scrub_pii` states which masking policy ran.

Console messages are attributed to the step that produced them
(`{"step": "4:click", "url": ".../workbench.ts", "line": 12}`), so a message
like "unsupported MIME type ('text/html')" can be traced to a module rather than
just observed. `worker_errors` reports Web Worker failures — an in-browser
computation that throws leaves the page looking correct and the result simply
never arrives.

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

### Request-level network enforcement

Every session installs Playwright request routing before the first navigation.
The same policy covers page resources, `fetch`/XHR, API steps, redirects and
WebSockets. Disallowed hosts and unsafe protocols are aborted; public hostnames
are DNS-resolved to reject private-address rebinding. `allow_private: true` is
the explicit exception and still requires the host to be in the allowlist.
The session `info` response and flow report include a bounded `network_policy`
report with allowed domains, request decisions, and blocked requests.

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
Screenshot baselines live in `<artifacts>/baselines/` and a frame that failed a
visual assertion in `<artifacts>/diffs/`.

### Why a step failed
A failed interaction or wait carries `failure_cause` instead of only a Playwright
call log:

```json
{"i": 4, "action": "click", "status": "failed", "reason": "harness_error",
 "error": "Playwright call log: waiting for locator",
 "failure_cause": {
   "found": true, "enabled": true, "in_viewport": false,
   "rect": {"y": 855}, "viewport": {"width": 390, "height": 844},
   "likely_cause": "rendered but outside the 390x844 viewport",
   "suggestions": ["[data-testid='skip']", "#skip", "button.primary"]}}
```

Also `covered_by` (what is painted on top), `hidden_by_css`, and a screenshot.
The suggestion list prefers `data-testid` — the only spelling that survives a
redesign. Diagnosis is best-effort and attached to *inconclusive* steps too, since
a Playwright timeout arrives as a bare exception; an adapter that cannot answer
reports `probes_available: false` rather than claiming the element was absent.
Selectors are resolved through Playwright's own locator, so engine spellings
(`text=Run safe`, `role=button[name=…]`) are diagnosed too rather than reported
as invalid CSS.

### Visual assertions
Canvas and WebGL content cannot be read back from the page — once a frame is
presented the drawing buffer is cleared, so `toDataURL` returns zeros. Visual
assertions therefore capture what the compositor showed:

```json
[{"action": "assert_canvas", "selector": "#viewport",
  "min_non_background_pct": 5, "min_colors": 3},
 {"action": "assert_screenshot", "name": "results-contour", "threshold": 0.005,
  "masks": ["#fps-counter"]}]
```

`assert_canvas` reports the measured numbers either way ("3768×1844, 41.2%
non-background, 27 colours, brightness 96.4") and requires at least one bound, so
a typo'd assertion cannot silently pass. `assert_screenshot` diffs against a
stored baseline; the first run of a missing baseline creates it and passes with
`baseline_created`.

Pixel thresholds are only comparable between runs because both the viewport
*and* `device_scale_factor` are pinned (1440×900 at dpr 2 by default). Leaving
the scale to the session fingerprint made the same flow rasterise at 1.25 and 2
on different runs, which resized every screenshot and moved the numbers these
assertions are made of.

Browser flows are exempt from the engine's 30 s request timeout: they are bounded
by their own per-step timeouts, and the cap used to return a bare `504` with no
step results for any flow that actually waited.

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

### Viewport and device

Geometry is **explicit and deterministic**: the default viewport is a fixed
1440×900 rather than whatever the session's rotated fingerprint produced (which
is why the same flow used to render at two different sizes). Every entry point
accepts the same knobs — `viewport` (`"1280x800"`, `[w,h]` or `{width,height}`),
`viewport_width`/`viewport_height`, `device` (preset: `desktop`, `laptop`,
`tablet`, `mobile`, `iphone_13`, `pixel_5`), `device_scale_factor`, `is_mobile`,
`has_touch`, `color_scheme`, `reduced_motion`, `screen` — on
`POST /browser/sessions`, `POST /run` and `POST /browser/sessions/{id}/run`.
CLI: `jambu qa test --viewport 390x844 --device mobile --color-scheme dark`.

A persistent session can therefore be taken to a phone width and screenshotted
deterministically, which matrix runs previously could not do.

### Responsive / locale matrix
`POST /browser/sessions/matrix` (MCP: `browser_test_matrix`) runs one flow
across viewports/locales concurrently (capped at the session limit). Variants set
`name` plus `viewport`, `device`, `locale`, `user_agent`, `device_scale_factor`,
`timezone_id`, `color_scheme`, `reduced_motion`, `is_mobile`, `has_touch`,
`screen`. Each variant returns a digest **and** the evidence it was built from —
`context_options`, `evaluated` (the values every `evaluate` step read),
`screenshots`, `cause` per failed step, and the full `report`.

### Export to Playwright
`POST /browser/sessions/export` (MCP: `browser_export_playwright`,
CLI: `jambu export flow.json --out app.spec.ts`) renders a flow as
`.spec.ts` so teams can move it into their own CI.

Dialog steps become a single `page.on('dialog', …)` listener plus a staged
`dialogAnswer` before the action that raises the dialog (Playwright only answers
a dialog whose listener was installed first). File picks and downloads become
`Promise.all([page.waitForEvent(…), click()])` races so the event is never
missed, and `wait` with a `js` predicate becomes `page.waitForFunction`.

### Import from Playwright
`POST /browser/sessions/import` (MCP: `browser_import_playwright`,
builtin tool, CLI: `jambu import app.spec.ts --out flow.json`) converts the
common `getBy`/keyboard/`expect` subset back into a flow. Every line it
cannot translate is reported with its line number — nothing is silently
dropped.

Coverage: `getBy*` (text/label/role/testid/placeholder/alt/title),
`locator()` (CSS/XPath), keyboard, waits (`waitForSelector/URL/Response` and
`waitForFunction` → a `js` wait), `expect` (+`not.`, counts, URL/title incl.
regex literals), locator declarations, multi-line chains, single-line
`test.step`, `page.evaluate` (becomes an `evaluate` step), `setInputFiles` and
`waitForEvent('filechooser')` races (become `upload` steps, `chooser: true` for
the picker form), `waitForEvent('download')` races plus
`expect(download.suggestedFilename())` (become `download` steps with `match`),
`dialogAnswer` staging and `expect(dialogs…)` (become `dialog` steps and
`assert_dialog`), and `page.route()` fulfill/abort (becomes the flow's `network`
policy). The dialog listener exported by `flow_to_playwright` is recognised and
dropped on import, so a spec round-trips without its scaffolding showing up as
junk. Reported, not guessed: control flow, fixtures, page objects, actions like
`dblclick`/`dragTo`, a hand-written `page.on('dialog')` handler (its logic
has no flow equivalent, so it is reported as `dialog-listener`), and any step
the exporter had to leave behind as a `// TODO unsupported …` comment (reported
as `export-gap-action`). Import also never re-grants `approve` from a comment —
uploads and gated clicks come back un-approved so whoever imports the spec
decides the gate again; only a `js` wait keeps its flag, because the
`waitForFunction` in the code is itself the opt-in.

### CLI
```bash
jambu plan "test login" --url http://localhost:3000
jambu test flow.json --local --trace --resolve-sources
jambu test flow.json --device mobile --color-scheme dark --network-idle
jambu test flow.json --no-scrub          # assert on raw numbers from a local app
jambu export flow.json --out login.spec.ts
```
`--json` prints the API response as-is — including `failure_cause`,
`evaluated` and screenshots — so a dashboard or CI job gets the same evidence
the API returns. Without it the CLI prints a one-line DOM verdict per failed step
("found, enabled, OFF-SCREEN … element at y=855 in 390x844 viewport") and each
evaluated value.

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
`JAMBU_MCP_PROFILE=developer` exposes only the eight high-level browser-testing
verbs (`browser_task`, `browser_test_flow`, `browser_test_plan`,
`browser_test_matrix`, `browser_session_run`, `browser_export_playwright`,
`browser_import_playwright`, `check_engine_health`), minimising tool-selection cost.

### Live view / human takeover
`GET /browser/sessions/{id}/screenshot` returns the current frame as base64;
`POST /browser/sessions/{id}/takeover {active}` pauses/resumes agent control
for CAPTCHA/2FA or visual checks.

While the flag is set, the engine **refuses agent mutations** on that
session (`human_takeover`, HTTP 403) but keeps observation allowed — a real
pause/resume loop, not a banner. The desktop pane now has a takeover toggle
(hand icon): it escalates the screencast quality, shows a human-control
banner, and flips the linked agent session's flag (session id remembered
locally). Remaining desktop milestone: real multi-webview tabs.

The desktop pane now has a **live view**: a Rust CDP `Page.startScreencast`
stream (`browser_start_screencast` / `browser_stop_screencast`) pushes JPEG
frames at ~30–60 FPS to the `useScreencast` hook, which the `ChromiumPane`
renders in place of the polled screenshot (polling remains the fallback).

**Dual-mode tabs:** a per-tab toggle switches between the CDP **stream** view
(default — automation and audits have full parity) and a **native** system
webview child (`browser_native_view`, positioned over the viewport). Native
mode gives real caret/selection/context menus but is a different engine: no
CDP input, no audits, no fingerprint scripts — the pane labels it. See
`docs/MULTIWEBVIEW_PLAN.md`.

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
- `docs/MCP_TOOLS.md` — generated MCP tool reference (52 tools).
