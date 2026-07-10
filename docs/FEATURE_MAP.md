# Jambubrowser — Feature Map (Harness / Browser / DeepNet / Developer)

> **Why this doc exists.** The product has grown to ~34k LOC of backend + ~6.7k LOC
> of frontend across 136 Python files and 44 TS/TSX files. The README's flat
> feature list no longer makes the product comprehensible. This document groups
> every feature into the four pillars the product actually ships, names the
> real problem each pillar solves, and points at the canonical files.

---

## The four pillars

| Pillar    | One-line value                                                | Real problem it solves                                                                 |
|-----------|---------------------------------------------------------------|----------------------------------------------------------------------------------------|
| **Harness**   | Run multiple AI agents in concert to do hard, multi-step work | "I need one model to plan, one to act, one to verify, and I need memory across runs."  |
| **Browser**   | Give the AI eyes and hands on the live web                    | "Audit my real webapp — not a screenshot, not a curl, the actual DOM and network."     |
| **DeepNet**   | Reach information the surface web hides                        | "Find what Google won't show, from onion routes to P2P-shared research."              |
| **Developer** | Embed Jambubrowser into other tools and workflows             | "I want this in my terminal, my CI, my editor, and my own product."                    |

Every feature in the repo maps to one of these four. The mapping is
defensible by file: if you can't grep the feature to a pillar, the feature
isn't real yet.

---

## 1. Harness — multi-agent orchestration

**Headline value:** Compose specialist agents (planner, critic, executor,
verifier) with shared memory and budgets, instead of trusting a single model.

### What exists today
- **Unified LLM layer** (`backend/llm/`, ~2k LOC) — 6 providers behind one
  protocol: Anthropic, OpenAI, Ollama, MLX, MiniMax, Mock. Auto-discovery,
  per-request cost tracking, smart routing (`cheapest` / `fastest` /
  `quality` / `fallback` / `local_only`).
- **Mixture-of-Agents provider** (`backend/llm/providers/moa.py`, 375 LOC) —
  fan-out to N reference models, then aggregate with a "thinking" model.
  Inspired by Nous Research Hermes; presets via `JAMBU_LLM_MOA_PRESETS`.
- **ReAct agent loop** (`backend/agent/loop.py`, 466 LOC) — Plan → Execute
  → Verify → Replan with SSE streaming, budget-aware (steps/tokens/seconds).
- **10 built-in tools** (`backend/agent/builtin_tools.py`, 420 LOC) — every
  capability wrapped as a typed async function with auto-derived JSON Schema:
  `web_search`, `scrape_url`, `vault_get`, `knowledge_query`,
  `memory_recall`, `memory_store`, `code_exec`, `goal_set`, `risk_check`,
  `final_answer`.
- **4-store memory** (`backend/memory/`, ~500 LOC) — user profile, session,
  semantic (embeddings), procedural (success rates). Hybrid retrieval
  (60% vector + 30% recency + 10% FTS).
- **HarnessX orchestrator** (`backend/agent/harness.py`) — multi-agent
  AEGIS pipeline, co-evolution, digester, evolution.
- **6 AI Employees** (`backend/employees/`, 8 files) — SecurityAuditor,
  PerformanceInspector, UXUIReviewer, SEOAnalyzer, AccessibilityAuditor,
  CodeQualityScout. Parallel dispatch from `backend/routes/audit.py`.
- **Goal orchestrator** (`backend/modules/goal_orchestrator.py`, 593 LOC) —
  sovereign goal lifecycle: create → activate → approach → block → fallback
  → achieve.
- **Skill synthesizer** (`backend/modules/skill_synthesizer.py`, 410 LOC) —
  detects failure → LLM writes a new tool → sandbox-tests → iterates →
  persists to DB.
- **Consensus engine** (`backend/modules/consensus_engine.py`) — multi-node
  proposal/vote/tally.
- **Sandbox executor** (`backend/core/sandbox.py`, 354 LOC) — subprocess
  + Docker backends, network isolation, 256MB cap, 30s timeout.

### Real problems this solves
- "I want a 12B local model to do most of the work but a 70B cloud model
  to arbitrate hard calls" → MoA presets.
- "I want my agent to remember what I told it last week and what worked"
  → procedural memory + success rates.
- "I want to give my agent tools, but I don't want to write tool-call
  JSON by hand" → auto-derived JSON Schema on every tool.
- "I want the agent to *write new tools* when it gets stuck" → skill
  synthesizer.

### What still hurts (improvement targets)
1. **MoA is registered as a provider but not in the LLM provider
   registry's auto-discovery path.** It lives in `providers/` but the
   health-check / fallback chain may not see it.
2. **No shared "plan library" across runs** — every agent run starts from
   zero; procedural memory exists but isn't consulted on plan generation.
3. **Goal orchestrator has 593 LOC but no visible UI surface** beyond
   `/goal/*` routes — users can't see goals in the app.

---

## 2. Browser — eyes and hands on the live web

**Headline value:** The audit/automation layer drives a real Playwright
browser, captures real telemetry, and exposes a DevTools-grade view of
the page being audited.

### What exists today
- **Inline browser pane** (`browser-app/src/components/browser/BrowserPane.tsx`,
  262 LOC) — iframe with tabs, URL bar, navigation, plus a devtools toggle
  wired to `devtoolsStore`.
- **Server-side web proxy** (`backend/routes/proxy.py`, 526 LOC) —
  fetches any URL and strips `X-Frame-Options` / `frame-ancestors` so the
  iframe can display sites that normally refuse framing. Path-based
  rewrite keeps relative `import()` and CSS `url()` resolving correctly.
- **DevTools-grade telemetry** (4 new tabs, 682 LOC total):
  - `ConsoleTab.tsx` (159) — captured console errors/warnings/logs
  - `NetworkTab.tsx` (210) — request waterfall, DNS/TCP/TTFB timing
  - `PerformanceTab.tsx` (203) — LCP/FCP/CLS, long tasks, navigation
  - `DevToolsPanel.tsx` (110) — orchestrator
  - Plus `devtoolsStore.ts` (270 LOC) — zustand store
- **Browser session manager** (`backend/modules/browser.py`) — session
  isolation, privacy wrapper, Playwright lifecycle.
- **Playwright scraper** (`backend/modules/playwright_scraper.py`,
  195 LOC) — dedicated JS-aware scraper.
- **Fingerprint rotator** (`backend/modules/fingerprint_rotator.py`) —
  generates unique user-agent / canvas / WebGL / audio fingerprints per
  session.
- **Form filler** (`backend/modules/form_filler.py`) — auto-detect forms,
  match to vault credentials, fill.
- **Vision grounding** (`backend/modules/vision.py`) — OCR, UI element
  detection, screen verification.
- **Computer use** (vision + `backend/modules/multimodal_input.py`) —
  macOS screen capture, mouse, keyboard.
- **Audit engine integration** (`backend/routes/audit.py`, 731 LOC) —
  Playwright-based data collection (network, console, DOM, a11y tree)
  feeds the 6 employees.

### Real problems this solves
- "Most websites block iframe embedding" → the proxy endpoint fixes
  that and is the single most important piece of browser plumbing.
- "I need to *see* what the audit is collecting" → the DevTools tabs
  turn opaque telemetry into a user-visible waterfall.
- "I need to look like a different browser every session" →
  fingerprint rotator.
- "I need to *do* things, not just read" → form filler, computer use,
  Playwright.

### What still hurts (improvement targets)
1. **DevTools data lives only in browser memory** — no way to export a
   network waterfall to a file, no way to compare two audits'
   performance traces side-by-side.
2. **Proxy has no cache layer** — every reload re-fetches upstream; even
   cache-bust aside, this is wasteful for repeated audit targets.
3. **Form filler is a stub from early phases** — no test coverage, no UI
   surface in the new app shell.
4. **No "record / replay" of browser sessions** for debugging failed
   audits.

---

## 3. DeepNet — reach what the surface web hides

**Headline value:** Multi-engine search, knowledge graph, Tor routing, P2P
federation — the information-access surface a normal browser can't offer.

### What exists today
- **Multi-engine metasearch** (`backend/modules/search.py`, 315 LOC) —
  SearXNG primary (90+ engines), DuckDuckGo fallback, Google last-resort.
  SOCKS-aware for Tor.
- **SearXNG fork** (`searxng/`, 142 MB vendored) — full source tree of
  the metasearch engine; config at `searxng-config/`.
- **SOCKS5 / Tor transport** (`backend/core/socks.py`, 94 LOC) — drop-in
  `make_async_client()` wrapper, lazy-imports `httpx-socks`.
- **Knowledge graph** (`backend/modules/knowledge_graph.py`) — entity
  extraction, relationship inference, topic clustering, persistence.
  Exposed via `/knowledge/*` (ingest, graph, search, stats, entity,
  clusters).
- **P2P discovery** (`backend/modules/p2p_discovery.py`) — UDP broadcast
  peer discovery for multi-node research mesh.
- **Federated RAG** (`backend/modules/federated_rag.py`) — query trusted
  peers for answers; routes at `/p2p/*`.
- **Risk shield** (`backend/modules/risk_shield.py`) — URL risk
  assessment before visiting.
- **Shadow browser** (`backend/modules/shadow_browser.py`) — autonomous
  background research without user interaction.
- **Privacy manager** (`backend/core/privacy.py`, 433 LOC) — 4 modes
  (Standard / Enhanced / Maximum / Local-Only), PII detection,
  tracking protection, network isolation.
- **Vault** (`backend/core/vault.py`, 567 LOC) — AES-256-GCM credential
  storage with PBKDF2 (480k iterations), machine-specific salt.
- **SSRF protection** (`backend/core/security.py`, 124 LOC) — `is_safe_url`
  blocks private IPs, DNS rebinding, unsafe schemes on every URL endpoint.
- **Missions** (`backend/modules/missions.py`) — cron-based background
  research scheduler.

### Real problems this solves
- "I want search results that aren't SEO-spam" → SearXNG over 90 engines.
- "I want to research a topic anonymously" → Tor + SOCKS routing.
- "I want my local nodes to share knowledge" → P2P + federated RAG.
- "I want to research a topic, leave, and come back to results" →
  mission scheduler + knowledge graph.

### What still hurts (improvement targets)
1. **Knowledge graph is write-heavy but has no "explore" UI** — there's
   a `KnowledgeMini.tsx` 2D force graph but no way to dive into an
   entity's full neighborhood.
2. **Federated RAG has no trust model surfaced to the user** — peers are
   "trusted" but the user can't see or revoke.
3. **Missions have no results browser** — you can schedule them but
   seeing what they collected requires diving into the DB.

---

## 4. Developer — embed Jambubrowser into other tools

**Headline value:** Other programs can use Jambubrowser as a library,
service, or agent — via CLI, MCP, eval framework, or plugins.

### What exists today
- **CLI tool** (`cli/`) — `jambu` command-line client + GitHub Action
  for CI/CD (`dd61dd7 feat(cli): jambu CLI tool + GitHub Action`).
- **MCP server** (`tools/mcp/`) — 21 tools exposed over Model Context
  Protocol, so Claude / Cursor / other MCP clients can drive Jambubrowser.
  Tests: `tests/test_mcp_server.py` (stdio smoke).
- **Eval framework** (`backend/eval/`, ~1.5k LOC) — harness, metrics,
  report, store, runners, 8 pre-defined task suites (`smoke`, `gaia`,
  `webarena_mini`, `webshop`, `swebench`, `memory`, `privacy`,
  `alfworld`).
- **Test council** (`tests/council.py`) — runs every gate in one command.
  Wired into `make test-council`.
- **Plugin system** (`backend/plugins/manager.py`) — load/configure
  plugins.
- **Supply chain verifier** (`backend/core/supply_chain.py`, 314 LOC) —
  SHA-256 hash check on every installed `.py` file, tracks
  known-good hashes in `~/.dependency_hashes.json`.
- **API key + billing** (`backend/routes/api_keys.py`, `backend/routes/billing.py`,
  `api_keys` + `audit_usage` tables) — programmatic access + per-key
  usage metering (billing is still a Stripe stub).
- **Teams** (`backend/routes/teams.py`, 196 LOC, 13 endpoints) — full CRUD
  for team-scoped finding assignments, activity feed, stats.

### Real problems this solves
- "I want `jambu audit https://my-app` in my terminal" → CLI.
- "I want my AI editor to be able to run Jambubrowser tools" → MCP.
- "I want to know if my change to a prompt made the agent worse" →
  eval framework + council.
- "I want this to fit into a CI gate" → GitHub Action.

### What still hurts (improvement targets)
1. **CLI lives in `cli/` but there's no packaging** — no `pyproject.toml`
   entry point, no `jambu` script wired in (now fixable since we added
   `pyproject.toml`).
2. **MCP server's 21 tools aren't enumerated anywhere in the docs** —
   users have to read source to know what's available.
3. **Eval tasks live in `backend/eval/tasks/` but there's no benchmark
   dashboard** — results are in `tests/.artifacts/council.json` JSON.
4. **Supply chain verifier has no "regenerate baseline" workflow** —
   after a legitimate dep update the user must re-hash manually.
5. **Plugin manager is minimal** — no plugin discovery, no manifest
   spec, no examples.

---

## Cross-cutting capabilities (don't fit one pillar)

These touch every pillar and are the platform's connective tissue:
- **Security middleware stack** (`backend/core/`) — 9 middlewares:
  access log, request ID, trusted host, security headers, request
  timeout, GZip, body size limit, rate limit, error sanitization.
- **Audit log** (`backend/core/audit.py`, 369 LOC) — SHA-256 hash chain,
  PII redaction, tamper-evident.
- **WebSocket layer** (`backend/engine_runtime.py`) — per-IP caps,
  client_id validation, broadcast helpers for agent state + audit.
- **Tauri 2 desktop shell** (`browser-app/src-tauri/`) — native
  orchestrator, deep links (`jambubrowser://`), auto-updater.
- **iOS companion** (`ios-app/`, 2,949 LOC SwiftUI) — GatewayService,
  KeychainService, BackgroundTasks.

---

## How to use this map

- **Adding a feature?** Decide its pillar first, then add the file path
  to that pillar's section in this doc.
- **Reviewing a PR?** The pillar grouping is the rubric — if a change
  doesn't fit any pillar, question whether it belongs.
- **Pitching the product?** Lead with the four one-line values from the
  table at the top.
