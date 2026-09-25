# Jambubrowser Improvement Plan — build, debug, and improve products with AI

**Status:** implemented through `main@91c5e7f` unless marked otherwise.
**Scope:** the agent-driven browser-testing platform: one-call test flows,
debugging telemetry, authoring, CI/CLI integration, and the desktop live view.
**Test evidence:** backend 1461 passed / 9 skipped; frontend 393 passed;
`cargo check` clean; live-verified against real Playwright + real localhost.

---

## 0. Operating instructions (read first)

### Start the stack
```bash
# Backend engine
.venv/bin/python -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001

# Desktop app (separate terminal)
cd browser-app && npm install && npm run tauri dev
```

### Verify your change before pushing
```bash
# Backend (excludes live-service tests)
.venv/bin/python -m pytest tests/ --ignore=tests/test_e2e.py \
  --ignore=tests/test_real_llm_integration.py \
  --ignore=tests/test_search_integration.py --ignore=tests/test_socks.py -q

# Desktop shell
cargo check                          # in browser-app/src-tauri
npm run typecheck && npm run lint && npm test   # in browser-app
```

### Token discipline (the project's prime directive)
Every new agent-facing surface must reduce calls or bytes versus the
primitive loop (`open → navigate → snapshot → act → snapshot → close`).
Concretely: batch work into single calls, return digests instead of DOM
dumps, attach telemetry instead of requiring follow-up calls, and count the
before/after in the docs.

---

## 1. What was built and how to use it

### 1.1 One-call test flows (the foundation)
**What:** `BrowserAgentSession.run_flow()` executes a declarative step list
in a real Playwright browser and returns one compact pass/fail report.
**Why:** a 10-step test used to cost 40–50 tool calls; now it costs one.

- HTTP: `POST /browser/sessions/run` (one-shot: open → run → close),
  `POST /browser/sessions/{id}/run` (existing session).
- MCP: `browser_test_flow(url, steps, …)`, `browser_session_run(session_id, steps, …)`.
- Internal agent: `browser_test_flow` builtin tool.
- CLI: `jambu test flow.json --url … --local [--trace --har --video --resolve-sources --json]`
  (exit 0 pass / 1 fail / 2 engine error — CI-ready).

Minimal example (one MCP call):
```
browser_test_flow(url="http://localhost:3000", local=true, steps='[
  {"action": "navigate", "url": "http://localhost:3000/login"},
  {"action": "type", "target": "Email", "value": "dev@example.com"},
  {"action": "click", "target": "Sign in"},
  {"action": "assert_visible", "target": "Dashboard"},
  {"action": "assert_console_clean"}]')
```

### 1.2 Element addressing: intent first, selectors when needed
**What:** steps address elements by `ref` (`@e3`), by human `target`
(exact name → `"role name"` → unique substring, with bounded candidates on
ambiguity), or by `selector` (CSS, `xpath=…`, `//…`).
**Why:** agents act without a prior snapshot round-trip; imported
Playwright specs (which use CSS) execute directly.
**Safety:** selector dispatch bypasses the risk classifier, so it always
requires `approve=true`; the post-action allowlist check still applies.

### 1.3 Local-dev support
**What:** `local=true` / `allow_private` permits loopback and private hosts —
*in addition to*, never instead of, the explicit domain allowlist
(`"allow_private"` is refused without it; `"*"` stays deny-all).
Console/page errors, failed requests, and ≥400 responses are captured
continuously and attached to every report — no separate telemetry calls.

### 1.4 Debugging capabilities
- **Network policy** (`network: {mocks, fail, delay, offline}`): test error,
  empty, loading, and offline states; `assert_made_request` / `assert_no_request`
  verify app traffic. First-match-wins.
- **Cause attribution:** every step reports the console errors, failed
  requests, and DOM delta it caused.
- **Artifacts:** `trace` / `har` / `video` capture Playwright trace, HAR, and
  video; paths return in the report.
- **Accessibility:** dependency-free probe
  (`assert_no_a11y_violations` — alt text, labels, names, `lang`, title,
  duplicate ids, tabindex, heading order).
- **Performance budgets:** `assert_lcp/fcp/load/dom_nodes/transfer_kb/
  resource_count` from Navigation/Paint timing plus an injected LCP/CLS
  observer (a `0` LCP means "no candidate observed").
- **Source maps:** built-in Base64-VLQ decoder maps console errors to
  original files (`resolve_sources: true`).
- **Determinism:** animations/transitions neutralised before each flow;
  `settle_ms=N` waits for resource-count quiet after navigations (HMR settle).

### 1.5 Authoring: from goal to flow
- **Plan:** `POST /browser/sessions/plan` (MCP `browser_test_plan`,
  `jambu plan "test login" --url …`, builtin tool). Template library
  (login, signup, checkout, search, accessibility, performance, responsive,
  smoke) works with no model; `use_llm=true` refines with the provider.
- **Meta-tool:** MCP `browser_task(url, goal, inputs)` plans, fills
  `{{placeholders}}`, and runs — one call end to end.
- **Record:** any client driving a session can capture it:
  `POST /{id}/record {active}`, `GET /{id}/flow`; credentials become
  replayable `{{email}}`/`{{password}}` placeholders, never stored verbatim.
  CLI: `jambu record --session <id> [--stop --out flow.json]`.

### 1.6 Scale-out and permanence
- **Matrix:** `POST /browser/sessions/matrix` (MCP `browser_test_matrix`)
  runs one flow across viewports/locales concurrently (capped at the session
  limit) with per-variant failure isolation.
- **Flow monitors:** `/browser/monitors` (CRUD + run + runs) re-runs stored
  flows on an interval, persists history, and alerts (desktop + webhook) on
  regression. Scheduler starts with the engine (`flow_monitors` tables).
- **Portability:** `POST /browser/sessions/export` → `.spec.ts`
  (`jambu export`, MCP `browser_export_playwright`);
  `POST /browser/sessions/import` ← `.spec.ts`
  (`jambu import`, MCP `browser_import_playwright`, builtin tool).
  The importer covers `getBy*`, `locator()`, keyboard, waits, `expect`
  (+`not.`, counts, regex URLs), declarations, multi-line chains,
  `test.step`, `page.evaluate`, and `page.route()` → network policy; the rest
  is reported with line numbers and reasons, never silently dropped. Our own
  exports round-trip with zero unparsed lines.

### 1.7 Semantic diffing
`POST /browser/sessions/semantic-diff`: structural diff over element catalogs
(added/removed/renamed/state) with a human summary, optional LLM explanation,
and optional vision description over screenshots (pixel diff fallback).

### 1.8 Human takeover + live view
- Backend: `POST /{id}/takeover {active}` flips `human_takeover`; the engine
  **refuses agent mutations** (HTTP 403) while observation stays live.
  `GET /{id}/screenshot` serves the current frame.
- Desktop: CDP `Page.startScreencast` streams ~30–60 FPS JPEG to the
  `useScreencast` hook (polled screenshot remains the fallback); toolbar
  takeover toggle with banner, boosted quality, and linked session flag;
  copy-page-text button; real find-in-page already runs in the live DOM.
- MCP profiles: `JAMBU_MCP_PROFILE=developer` keeps only the 8 high-level
  testing verbs (`browser_task`, `browser_test_flow`, `browser_test_plan`,
  `browser_test_matrix`, `browser_session_run`, `browser_export_playwright`,
  `browser_import_playwright`, `check_engine_health`); `curated` drops
  `execute_tool`. MCP surface: **42 tools**.

### 1.9 Dev-server awareness
`GET /browser/dev-servers` scans common ports and identifies frameworks
(Vite/Next/CRA/Nuxt/Remix/SvelteKit/Astro/Angular/webpack/Django);
`/probe?url=` inspects one URL (`jambu dev-servers`).

---

## 2. Verification evidence (how we know it works)

- **Unit/integration:** 71 backend test files; flow, debug, plan, codegen,
  monitor, dev-server, and route suites run against scripted fake pages
  (no browser needed); receipts, PII scrubbing, and approval gates pinned.
- **Live:** real Playwright against a real `http.server` on localhost —
  intent clicks, selector dispatch, `evaluate`, network mocks (a 404
  disappeared), trace/HAR artifacts, dev-server scan, and HMR settle all
  confirmed end to end.
- **Frontend:** 42 test files / 393 tests incl. screencast, takeover, and
  copy-text suites; `tsc`, ESLint, and `cargo check` clean.

---

## 3. Remaining work (explicit, with plans)

### 3.1 Multi-webview tabs — dual-mode implemented, hardening remains
See `docs/MULTIWEBVIEW_PLAN.md`. Dual-mode tabs now ship: the CDP **stream**
view (default, automation/audit parity) and a **native** system-webview child
(`browser_native_view et al.`) for reading/forms/CAPTCHA, with a per-tab
toggle and an explicit "no audits" badge. Remaining: Phase-1 measured spike on
a real desktop build, Phase-3 parity shims (downloads/find/copy in native
mode), and Phase-4 hardening (crash fallback, security review of what native
mode does not inherit).

### 3.2 Full-fidelity spec parsing — bounded by design
Static translation of fixtures, page objects, and control flow is not
achievable; the importer reports them with reasons instead. If worth more
investment, in order: (a) `test.step` block scoping for shared setup,
(b) `storageState`/fixture seeding via the existing `storage_state` session
option, (c) data-driven `test.each` expansion into matrix variants.

### 3.3 Suggested next increments (all finishable)
1. Coverage: export `evaluate`-free flows only (flag JS-dependent steps) and
   report uncovered assertions/error states.
2. Improve importer fidelity for `test.step`, `storageState`/fixture seeding,
   `test.each`, page objects, and parameterized environments.
3. Publish `jambu` to PyPI/Homebrew so `jambu test` is one install away.
