# Jambubrowser v3.4.0 — Capability Report + Gaps

Date: 2026-10-03 | Commit: `f81d35b` | Branch: `main` | Version sync: `3.4.0` (pyproject + backend + browser-app + tauri)

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

### Dynamic VPN (`docs/VPN.md`)
Layered egress: a **tunnel** (WireGuard/OpenVPN) as base + a rotating **proxy pool** on top, behind `VPNManager.resolve_proxy()`. Rotation (`failover`/`round_robin`/`random`/`least_latency`), per-session stickiness independent of policy, consecutive-failure quarantine with self-healing, EWMA latency, optional background health probe. Fail-closed by default (`JAMBU_VPN_FAIL_OPEN=1` opts out); credentials redacted everywhere. Wired into `make_async_client()` (all outbound HTTP) and `BrowserSession` (sticky endpoint at launch). `GET /vpn/status|config`, `POST /vpn/select|probe`, CLI `jambu vpn status|up|down`, MCP `vpn_status|vpn_select|vpn_probe`. Inert unless `JAMBU_VPN_ENABLED=1`; `JAMBU_VPN_DRY_RUN=1` for rootless/CI. 96 tests.

### Decentralized / Agent-platform
DCM provider + `/dcm/*` + 5 MCP + `jambu dcm`; MeshPay epochs/Merkle/memo-anchor + payouts prepared/broadcast + 12 routes; x402 paywall + atomic nonce-claim fix; eval certs frozen spec_hash; verification SIGNED<CANARY<REDUNDANT<ATTESTED-no>; A2A card + SendMessage/GetTask/CancelTask; Ed25519 evidence + standalone verifier; Remote MCP `/mcp/` 52 tools, profiles full/curated/developer(8); **simulation compute** — verified replicated jobs on the mesh, settled only on numeric agreement (`docs/SIMULATION_COMPUTE.md`).

### Developer + Desktop
Factory `engine.py ~300 lines`, 34 included routers (~290 endpoints), 9 middlewares, WS, `/v1` OpenAI-compat, `/v2`; CLI 20 groups (~1750 lines) + PyPI/Homebrew packaging; eval/council, plugins, supply-chain, API-keys, teams; Tauri CDP Chromium + screencast 30-60FPS + takeover + dual-mode stream|native; AppShell 20+ panels + zustand; iOS 2949 LOC.

## 2. Gaps (explicit, tracked)
1. Native tabs: Phase-3 shims missing (downloads/find/copy disabled in native), no crash fallback, no privacy-script review — see `docs/MULTIWEBVIEW_PLAN.md:86-92`.
2. Importer: fixtures/page-objects/control-flow not translated (reported with reasons by design).
3. Publishing: PyPI/Homebrew runbook ready (`docs/PUBLISHING.md`) but not published; version-sync test exists.
4. AEGIS mutation paths are library-only by design (read surface shipped 2026-10-03: `GET /agent/aegis/configs`, `/configs/latest`). Exposure as a mutation API is a product decision. (MoA is **not** missing from auto-discovery — a fresh `ProviderRegistry` resolves all 8 providers including `moa` on miss, verified 2026-10-03. Plan-library cache shipped 2026-10-03 — `/agent/plan-library` — closing that part too.)
5. P2P/federated single-node inert; knowledge neighborhood explorer shipped 2026-10-03 (`GET /knowledge/entity/{id}/neighborhood`, direct-connection `get_entity` bug fixed); missions results viewer (`MissionResultsViewer.tsx`) exists — comparison/export UI within it is still thin.
6. Billing Stripe stub; sidecar no longer assumes system python3 (Tauri orchestrator prefers `.venv/bin/python`, verified cargo check 2026-10-03); deep-link handler + updater UX TODO; ATTESTED no hardware; MeshPay paging/forward-markets/auto wallet-binding open.
7. VPN: no real-tunnel integration test (needs root + a vendor binary, so CI only covers dry-run). Pool state now persists via `JAMBU_VPN_STATE_FILE` (atomic JSON, age-bounded), closing the restart-amnesia gap.
8. Env drift **resolved 2026-10-03**: `.venv` now carries the real dependency set from `requirements.txt` (pytest-asyncio included, which clears the 24 "async def not supported" failures the shim run misattributed), Playwright Chromium is installed, and `/tmp/jambu_stubs` is superseded. Full backend suite (real deps, no shims): **1878 passed / 9 skipped / 0 failed**; the previously shim-blocked suites (`test_socks.py`, `test_mcp_remote.py`, `test_mcp_server.py`, `test_meshpay_payouts.py`, `test_eval_cli.py`) now run too — 48 passed, 1 env-skip. Frontend: typecheck clean, lint 0 errors, **471 tests / 48 files**, `cargo check` clean.

### Corrections to v3.3.0
Three items previously listed as open next increments were **already shipped**:
- *Coverage flag evaluate-steps* — `forbid_evaluate` is wired end-to-end
  (`browser_agent.py`, `routes/browser_sessions.py`, `mcp_server.py`,
  `builtin_tools.py`, CLI `--forbid-evaluate`), landed in `9eaf7ea`.
- *Importer `test.step`/`storageState`/`test.each`* — `_extract_each_blocks`,
  `storage_state_path`, and `test.step` unwrap are in `browser_codegen.py`
  (`9eaf7ea`), tested at `test_browser_codegen.py:360-428`.
- *Goals UI* — `GoalsPanel.tsx` plus tests, wired into `App.tsx`/`Sidebar.tsx`
  (`d44fcb9`).

Scale figures were also understated: backend is **57,755** LOC across 196 files
(not ~53.9k); frontend 21,971 (as stated).

## 3. Next finishable increments
Two-node smoke test (P2P/mesh/consensus) → native-tab parity + hardening (downloads/find/copy/crash-fallback/privacy review) → MeshPay receipt export → Stripe checkout or roadmap label → AEGIS mutation surface (product decision).
Full per-breadth depth audit: `docs/BREADTH_DEPTH.md`.
