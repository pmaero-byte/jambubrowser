# Jambubrowser v3.3.0 — Capability Report + Gaps

Date: 2026-09-30 | Commit: `279e5c4` | Branch: `main` clean | Version sync: `3.3.0` (pyproject + backend + browser-app + tauri)

> Source of truth: README, docs/FEATURE_MAP.md, docs/CHANGELOG.md, docs/IMPROVEMENT_PLAN.md, docs/BROWSER_TESTING.md, docs/BROWSER_SESSIONS.md, docs/MCP_REMOTE.md, docs/EVAL_CERTIFICATES.md, docs/EVIDENCE.md, docs/VERIFICATION.md, docs/MESHPAY.md, docs/X402.md, docs/A2A.md, docs/CI.md, docs/MULTIWEBVIEW_PLAN.md + live CLI `jambu --help`.

## 1. Capabilities improved so far

### Harness (multi-agent orchestration)
- Unified LLM: 7 providers (Anthropic, OpenAI, Ollama, MLX, MiniMax, DCM, Mock) + MoA fan-out (`backend/llm/providers/moa.py`), routing cheapest/fastest/quality/fallback/local_only, cost tracking.
- ReAct loop (`backend/agent/loop.py`): Plan→Execute→Verify→Replan, SSE, budgets, `/v2/agent/run`, history.
- 10 built-in tools, 4-store memory (profile/session/semantic-vec/procedural, 60/30/10 hybrid).
- 6 AI Employees (Security, Performance, UX/UI, SEO, Accessibility, CodeQuality) parallel from `backend/routes/audit.py`.
- Goals (593 LOC), Skill synthesizer (failure→LLM tool→sandbox→persist), Consensus, Sandbox (256MB/30s).

### Browser (eyes + hands) — biggest Sept delta
- One-call flows: `POST /browser/sessions/run`, MCP `browser_test_flow`, `jambu test` — 40-50 calls → 1, telemetry attached, savings at `GET /browser/sessions/savings`.
- Addressing: `ref @eN` / human target / selector CSS-XPath (`approve=true`) / `evaluate` JS.
- Debug: mocks fail/delay/offline, cause attribution, trace/HAR/video, source-maps, a11y/perf budgets, storage_state, HMR settle.
- Authoring/scale: `POST /sessions/plan`, `browser_task` meta-tool, recording→`{{placeholders}}`, viewport matrix, flow monitors, Playwright export+import round-trip, semantic-diff.
- Rails: allowlist fail-closed (`*`=deny-all), Playwright request routing, risk gates, PII scrub, Merkle receipts, `403 human_takeover`, compact/delta snapshots.
- Classic: proxy strips X-Frame-Options, scraper, fingerprint rotator, form-filler+vault, vision/computer-use, DevTools 5 tabs + HAR/CSV.

### QA team (Milestones 1-3)
Managed cases, NL `POST /qa/from-goal`, datasets `row→env→vault`, flake (retry→flaky→quarantine 409→promote), API steps via browser context, JUnit+SARIF QA001, codegen parity, case×dataset×viewport matrix, `/qa/overview` + QaPanel.

### Audit wedge
Exports SARIF/JSON/MD/HTML, shared `_audit_event_stream`, monitors (new/resolved/persisting + visual-diff 2%), `jambu monitor`, Monitors/FlowMonitors panels, CI `action.yml` + `jambu audit/quick --fail-on` exit 0/1/2.

### DeepNet
SearXNG 90+ engines, swarm `deep_research`, ArXiv/GitHub/YouTube, knowledge-graph + KnowledgeMini, missions + `jambu diff`, Tor/SOCKS, 4 privacy modes, AES-256-GCM vault, SSRF `is_safe_url`, risk-shield.

### Decentralized / Agent-platform
DCM provider + `/dcm/*` + 5 MCP + `jambu dcm`; MeshPay epochs/Merkle/memo-anchor + payouts prepared/broadcast + 12 routes; x402 paywall + atomic nonce-claim fix; eval certs frozen spec_hash; verification SIGNED<CANARY<REDUNDANT<ATTESTED-no>; A2A card + SendMessage/GetTask/CancelTask; Ed25519 evidence + standalone verifier; Remote MCP `/mcp/` 45 tools, profiles full/curated/developer(8).

### Developer + Desktop
Factory `engine.py ~250 lines`, 20+8 routers, 340 handlers, 9 middlewares, WS, `/v1` OpenAI-compat, `/v2`; CLI 19 groups (~1700 lines) + PyPI/Homebrew packaging; eval/council, plugins, supply-chain, API-keys, teams; Tauri CDP Chromium + screencast 30-60FPS + takeover + dual-mode stream|native; AppShell 20+ panels + zustand; iOS 2949 LOC.

## 2. Gaps (explicit, tracked)
1. Native tabs: Phase-3 shims missing (downloads/find/copy disabled in native), no crash fallback, no privacy-script review — see `docs/MULTIWEBVIEW_PLAN.md:86-92`.
2. Importer: fixtures/page-objects/control-flow not translated (reported with reasons by design).
3. Publishing: PyPI/Homebrew runbook ready (`docs/PUBLISHING.md`) but not published; version-sync test exists.
4. MoA not in auto-discovery; no plan-library cache; goals no UI; AEGIS ~1.8k LOC library-only.
5. P2P/federated single-node inert; knowledge no neighborhood explorer; missions results browser thin.
6. Billing Stripe stub; sidecar python3 assumption; deep-link handler + updater UX TODO; ATTESTED no hardware; MeshPay paging/forward-markets/auto wallet-binding open.
7. Env drift in this shell: system python3.14 lacks `sqlite-vec`, `.venv` lacks `dotenv/httpx/pytest` — use README venv + `pip install -r requirements.txt`.

## 3. Next finishable increments
Coverage flag evaluate-steps → importer test.step/storageState/test.each → publish jambu → native parity + hardening → Jira/Linear export + per-step SARIF + goals UI.
