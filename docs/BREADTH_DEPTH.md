# Breadth × Depth map — every surface, its current depth, and its next depth fix

Engineering audit, 2026-10-03 (v3.4.0, branch `main` @ `96b95fc`).
"Depth" here means: is it wired into a route/CLI/MCP caller, does it have
tests, and does it fail safely — not just "does a module exist".

Legend: **●** wired + tested | **◐** wired, partial/inert in practice | **○** library-only or stub

## Product breadths

| # | Breadth | Depth | Why / next depth fix |
|---|---------|-------|----------------------|
| 1 | One-call browser testing (`browser_task`, `browser_test_flow`, `jambu test`) | ● | Full: HTTP route, MCP tool, CLI; Playwright export/import round-trip |
| 2 | Debug loop (mocks, cause attribution, trace/HAR/video) | ● | Breadcrumbs attach to a single report; no follow-up round-trips |
| 3 | QA team (cases, datasets, matrix, JUnit/SARIF, quarantine) | ● | Routes + CLI + panel; flake→quarantine→promote states tested |
| 4 | Audit wedge (findings, SARIF/JSON/MD/HTML exports, monitors) | ● | Export verified; Jira/Linear payload export added this pass |
| 5 | Per-step browser-flow SARIF | ● | `flow_report_to_sarif`, `jambu test --sarif`, tests |
| 6 | Dynamic VPN egress | ● | Tunnel+pool, fail-closed, state persistence, 88 tests, MCP surface; **no real-tunnel test** (needs root) |
| 7 | Decentralised simulation compute | ● | Queue worker, numeric verification, idempotency, evidence signing, 7 queue tests; **no multi-process fan-out** |
| 8 | MeshPay / USDC settlement (epochs, Merkle, receipts, payouts) | ● | Audited receipts + reconciliation windows; **paging still caps at 200 receipts/fetch (docs/MESHPAY.md)** |
| 9 | x402 paywall | ● | DEFAULT_PAID_ROUTES honored, nonce-claim atomicity fixed earlier |
| 10 | DCM provider (inference, ReceiptLedger) | ● | Routes + 5 MCP tools + CLI |
| 11 | Remote MCP (Streamable HTTP + server card) | ● | 52 tools, auth boundary, `--check` docs freshness gate |
| 12 | Agent loop (ReAct, tools, memory) | ● | Procedural-memory hints wired; plan library added this pass |
| 13 | Plan library | ● | 9 tests; advisory templates + per-template success rates |
| 14 | AEGIS evolution | ◐ | Now has read surface (`/agent/aegis/configs*`); mutation stays library-only **by product decision** |
| 15 | Knowledge graph | ● | Entity/relations/clusters/stats + neighborhood explorer this pass |
| 16 | Missions | ◐ | Scheduler, results routes, results viewer exists; comparison/export UI thin |
| 17 | Goals | ● | GoalsPanel UI + /goal/* |
| 18 | Memory (4 sub-stores) | ● | Hybrid retrieval 60/30/10, forget, per-user scoping |
| 19 | Eval certificates | ● | Frozen spec_hash, Ed25519 sign/verify, 9 suites |
| 20 | Evidence bundles | ● | compute_simulation + audit export kinds, standalone verifier script |
| 21 | A2A agent | ● | Card + SendMessage/GetTask/CancelTask |
| 22 | Models / LLM registry | ● | 8 providers auto-discovered incl. MoA (verified 2026-10-03) |
| 23 | P2P discovery / federated RAG / consensus | ○ | Routes/tools exist but are single-node-inert; no second node in CI. **Depth fix: a two-node LAN smoke test, then show/hide UI toggle** |
| 24 | Harness bridge | ○ | Experimental-gated, talks to localhost:9090 infra not shipped. **Depth fix: package verbatim or remove from the shipped story** |
| 25 | Billing / Stripe | ○ | Stub `billing.py`; TEAMS/Pro tiers exist but no checkout. **Depth fix: real Stripe or mark as roadmap in docs** |
| 26 | Desktop (Tauri) | ● | CDP screencast/takeover/native-mode opt-in; sidecar now prefers venv python; `cargo check` clean. Native-tab parity items remain (downloads/find/copy/crash-fallback/privacy review) |
| 27 | iOS app | ○ | ~2.9k LOC Swift, not part of the desktop CI path; needs Xcode/mac runner to verify |
| 28 | CI / packaging | ● | 3.4.0 artifacts pass `twine check`; MCP docs generator has a --check freshness gate; version-sync test passes |

## Cross-cutting risks

1. **No second node anywhere in CI** — mesh, P2P, consensus, federated RAG
   are single-node artifacts by construction. One integration script that
   boots two engine processes would flip most of the ◐/○ rows above.
2. **Native tabs are pre-production** — disabled features + no crash
   fallback + no privacy review. Disabled-by-default is acceptable for now.
3. **Async test mode was silently broken** until real deps were installed
   (shimmed `pytest-asyncio` apparently loaded; the suite's 24 "async not
   supported" failures were the same underlying environmental gap).
   The fix is already in place and the count is green.
4. **Doc drift** — several "shipped" claims had been merged ahead of the
   code this session (VPN/simulation untracked, tool counts, gap list).
   The source-of-truth files now agree; keep the `docs/MCP_TOOLS.md`
   `--check` test and the marketing-claims review in the release checklist.

## Depth-fix ordering (what to build next)

1. **Two-node smoke test** for P2P/federated/consensus/DCM mesh (flips the
   largest ○ row to ◐).
2. **Native-tab parity**: downloads/find/copy shims, crash fallback,
   privacy-script review (desktop runtime needed).
3. **MeshPay receipt export** to lift the 200-per-fetch cap.
4. **Stripe checkout wiring** or an explicit "roadmap" label.
5. **AEGIS mutation API** — explicitly a product decision; the read
   surface is ready when the mutation story is.
