# Changelog

All notable changes to Jambubrowser.

## [Unreleased]

### Added — E7: verification tiers for paid compute (canaries + sampled redundancy)

The DePIN verification ladder, implemented where this engine can honestly
climb it:

- **`backend/modules/verification.py`** — tier model
  (`SIGNED` < `CANARY` < `REDUNDANT` < `ATTESTED`, the last explicitly
  *not implemented*), a value-at-risk policy
  (`required_tier(price_usdc)`, env-tunable thresholds), pluggable
  executors (a worker is any async `run(text) -> str`), and sealed runs
  (`execution_hash` per execution).
- **Canaries** — known-answer probes with must-contain checks; PASS/FAIL per
  worker, recorded.
- **Redundant execution** — a second executor runs the same task and outputs
  are compared (`exact` or `similarity` with a tolerance); verdicts
  `MATCH`/`MISMATCH`/`ERROR` carry both execution receipts. Divergence is
  what a substituted model or truncated output looks like.
- **Scorecards** — `GET /verification/workers` aggregates canary pass rate
  and redundancy agreement per worker; `faulty-echo` ships as a clearly
  labelled fault-injection double so the detection path is demonstrable.
- **Evidence** — `POST /verification/evidence` signs the verdict window into
  a `compute_verification` bundle (verified by the standalone script).
- **Routes** — policy, redundant, canary, workers, verdicts, evidence.
- **Live-verified**: canaries PASS on `echo` and `mock-llm`, FAIL on the
  faulty double; `mock-llm×2` redundancy MATCH (agreement 1.0) while
  `echo×faulty` MISMATCH (agreement 0.6087 < 0.85 tolerance); scorecards
  scored the mismatch; the signed bundle verified with exit 0.
- **Tests (+24)** — tier policy, comparators (drift vs substitution),
  sealing determinism, canary pass/fail per worker, redundancy
  MATCH/MISMATCH/ERROR, unknown worker/comparator rejection, scorecard
  aggregation (scoped worker ids because the verdict DB is shared), evidence
  verification, and route validation. Docs: `docs/VERIFICATION.md` with the
  honest limits (lexical similarity, explicit sampling cost, no
  auto-suspension, ATTESTED marked not-implemented).

### Added — E6: A2A agent (other agents can hire this engine)

Jambubrowser now speaks the Agent2Agent protocol (v0.3 JSON-RPC binding) as
a hireable worker:

- **`/.well-known/agent-card.json`** — public card with three skills
  (`audit_web_app`, `agent_eval_certify`, `mesh_inference`), bearer security
  scheme, and honest capabilities (`streaming: false`,
  `pushNotifications: false`).
- **`backend/modules/a2a.py`** — JSON-RPC methods `SendMessage` (blocking by
  default), `GetTask`, `CancelTask` with spec-shaped Task/Message/Artifact
  objects, the `TASK_STATE_*` enum, A2A error codes (-32001/-32002/-32003/
  -32004) and the `@type` error-info payloads. Tasks persist in `a2a_tasks`
  with message history (`ROLE_USER` → `ROLE_AGENT`); workers can be
  cancelled; a task orphaned by an engine restart is marked FAILED with the
  reason on first read.
- **Three access doors**: engine API key, an **x402 payment** when the
  paywall is enabled (`POST /a2a` is now a paid route, `a2a_task` $0.05 —
  the middleware stamps `x402_paid` on the scope so anonymous agents can
  hire us in USDC), or `JAMBU_A2A_OPEN=1` for local dev. Everything else
  gets a `401` **with** a `WWW-Authenticate` challenge — which exposed and
  fixed an engine bug: the custom HTTPException handler was dropping
  `exc.headers`, silently mangling every challenge response.
- **Live-verified**: card served; unauthenticated call 401 + challenge;
  with an agent key, a text-only URL message inferred `audit_web_app` and
  returned a completed task from a **real Playwright audit** (`audit_id=1`),
  and a data-part message ran `agent_eval_certify` to a `PASS` certificate;
  `GetTask` returned the full history.
- **Tests (+18)** — card shape (public even when RPC requires auth),
  dispatch (unknown method, invalid request, honest streaming/push refusals,
  missing/unknown skill listings, URL inference), lifecycle (blocking
  completion + artifacts, non-blocking + polling, failing skill → FAILED,
  unknown task, cancel + not-cancelable, orphan recovery), and auth (401
  default, API key, x402-paid access, 402 without payment when enabled).
- Docs: `docs/A2A.md` (skills table, access doors, honest not-done list).

### Added — E5: agent-evaluation certificates (frozen spec, coverage, signed verdicts)

The eval harness (9 suites) now issues **certificates** instead of scores
you have to trust:

- **`backend/modules/eval_cert.py`** — freezes the experiment definition
  (sorted task ids, scoring rule, provider/model, threshold, timestamp) and
  hashes it **before** the run; judges coverage first; then PASS/FAIL/
  INCONCLUSIVE/INVALID; signs the whole thing as an `agent_eval` evidence
  bundle (E3, Ed25519).
- **Coverage is a first-class check** — missing, extra, duplicate or
  malformed results make the certificate ``INVALID``, so dropping the tasks
  you failed is detectable rather than "mostly passed".
- **Verdicts are recomputable** — `INCONCLUSIVE` when harness errors make a
  below-threshold score untrustworthy; `GET /eval/certificates/{id}`
  re-derives the verdict from the embedded results, rejecting even a
  correctly signed certificate that lies about its own verdict.
- **Routes** — `GET /eval/suites`, `POST /eval/certificates`,
  `GET /eval/certificates[/{id}]`; `/eval/` is exempt from the request
  timeout (suite runs are long).
- **MCP tools (35 total)** — `agent_eval_certify`, `agent_eval_verify`.
- **Live-verified with the real harness** (mock provider, smoke suite):
  2/5 passed → `PASS` at threshold 0.4 and `FAIL` at 0.99 on the same
  results; verification checks all true; standalone verifier exit 0;
  dropping one result from the certificate → `INVALID` (exit 1).
- **Tests (+19)** — spec-hash stability/order-independence, every verdict
  branch (thresholds, missing/extra/duplicate/malformed, errors →
  INCONCLUSIVE, errors above threshold → PASS), signed certificate
  round-trip, signature tamper, **lying-verdict rejection**, standalone
  verifier acceptance, and route tests with an injected runner.
- Docs: `docs/EVAL_CERTIFICATES.md` (verdict table, live example, honest
  limitations: single-run variance, harness-mode coverage, no revocation).

### Added — E4: browser sessions as a service (the agent loop, with rails)

Agent-driven browsing now has the perception pattern the category validated
(snapshot → typed catalog with refs → deterministic dispatch) plus the
safety rails browser agents need:

- **`backend/modules/browser_agent.py`** — sessions carry a **domain
  allowlist** (fail closed on empty), **approval gates** (session-level plus
  an always-on risk classifier for buy/pay/delete/send/transfer/… words),
  **PII scrubbing** of snapshot text/names/hrefs through the shared
  detector, and a **hash-chained receipt log** per step with a Merkle root.
  Navigations, redirects and link clicks are all allowlist-checked; a
  disallowed landing page is reverted to `about:blank` and recorded.
- **Routes** — `/browser/sessions` open/list/info, `…/navigate`,
  `…/snapshot`, `…/act`, `…/receipts`, `…/evidence` (signed session bundle
  via E3), and DELETE to close. Refusals are 403s with machine-readable
  reasons (`blocked_domain`, `approval_required`, `unsafe_url`,
  `unknown_ref`, `session_limit`).
- **MCP tools (33 total)** — `browser_session_open|snapshot|act|receipts|close`.
- **Live-verified with real Playwright**: opened an ephemeral session
  allowlisted to example.com, navigated, snapshotted the catalog, got 403 on
  `iana.org`, got 403 on the *external link click* (refused before the
  click), produced a 3-step receipt log (`ok, ok, blocked`), signed it into
  an evidence bundle the standalone verifier accepted, and closed cleanly.
- **Two real bugs found by the live run**: `BrowserSession.get_page` did
  not exist (added a public accessor), and the MCP tool-doc generator was
  documenting module *helpers* as tools (now reads the FastMCP registry —
  33 tools, not 34).
- **Tests (+24)** — allowlist (subdomain semantics, blocked navigation,
  SSRF, redirect revert, external-link pre-check, sneaky post-click revert),
  approval gates (session gate, always-risk, unknown refs), PII scrubbing
  (on/off), receipt chain recomputation + Merkle root, session evidence
  verification, service caps/TTL, and the full route flow. Docs:
  `docs/BROWSER_SESSIONS.md` with the honest not-done list.

### Added — E3: signed evidence bundles with a standalone verifier

Claims about what the engine observed can now be signed so a third party
verifies them **without running, installing, or trusting the codebase**:

- **`backend/modules/evidence.py`** — Ed25519 bundles over three kinds:
  saved audit reports (`audit_report`), x402 receipt windows
  (`x402_receipts`, including the Merkle root), and independent DCM
  settlement-chain verdicts (`dcm_settlement`). The exact canonical bytes
  (MeshPay's JS-faithful serializer) are embedded and signed; the signing
  statement also covers version/kind/created_at/subject, so metadata cannot
  be swapped. Keys come from `JAMBU_EVIDENCE_KEY` or a `0600` file
  (`~/.jambu/evidence_ed25519.key`, generated on first use).
- **`scripts/verify_evidence_bundle.py`** — standalone verifier: stdlib +
  `cryptography` only, no project imports. Checks required fields,
  supported version/algorithm, `sha256(payload_canonical) == payload_hash`,
  canonical↔payload equivalence, statement hash, and the Ed25519 signature.
  Exit 0/1/2 with per-check PASS/FAIL output and the signer fingerprint.
- **Routes** — `GET /evidence/key`, `POST /evidence/{audit/{id},x402-receipts,
  dcm-settlement,verify,anchor}`, `GET /evidence/bundles[/{id}]`.
  Anchoring writes the bundle's `payload_hash` via the MeshPay anchor module
  (Solana memo program or the labelled mock transport) and refuses to
  double-anchor.
- **Storage** — `evidence_bundles` table keeps the canonical payload so a
  stored bundle reconstructs byte-exactly (ship-blocking bug found live:
  version/algorithm weren't persisted, so fetched bundles failed
  verification — migration added).
- **Live-verified**: paid audit → signed audit bundle → **standalone
  verifier exit 0 (VALID)** with fingerprint; tampered bundle → **exit 1
  (INVALID)**; x402 receipts bundle with Merkle root → VALID → anchored
  (mock); DCM settlement bundle (chain_valid=True, checked=3, matching
  DCM's own verdict) → VALID.
- **Tests (+21)** — key management (env/file, 0600, fingerprint stability),
  roundtrip validity, JS-style number serialization in the canonical bytes,
  eight parametrized tamper cases, standalone verifier accept/reject/usage
  exit codes, audit/x402/DCM bundle sources, anchor + conflict, and the
  verify endpoint. Docs: `docs/EVIDENCE.md` (including what a signature
  does and does not prove).
- Also: MCP session-manager reset moved into the shared test-isolation
  fixture (the SDK allows one run per instance; per-file resets were
  whack-a-mole).

### Added — E2: x402 paywall (agents pay per call in USDC)

Metered endpoints can now be sold over **x402 v2** — HTTP-native agent
payments (165M+ transactions, ~98.6% USDC, sub-$0.31 average):

- **Spec-exact wire objects** (`backend/modules/x402.py`): 402 responses
  carry the base64 ``PAYMENT-REQUIRED`` header with a v2 ``PaymentRequired``
  object (CAIP-2 network, atomic-unit amount, ``payTo``, USDC ``extra``);
  clients retry with ``PAYMENT-SIGNATURE``; buffered responses carry
  ``PAYMENT-RESPONSE`` with the settlement.
- **Charged routes**: ``POST /audit/quick`` ($0.02), ``POST /audit/run``
  ($0.10), ``POST /dcm/infer`` ($0.001) — env-overridable prices, Base
  Sepolia + its USDC as safe defaults, Base mainnet USDC documented.
- **Authorization flow** (verify → resource → settle) implemented as a raw
  ASGI middleware because FastAPI dependency teardown cannot attach the
  settlement header; buffered bodies are held until settlement so
  ``PAYMENT-RESPONSE`` is present, while SSE audits stream and settle at
  body end with the receipt as the record.
- **Honest money semantics**: mock facilitator is the default and is
  labelled in ``/x402/config`` and every receipt; non-2xx responses are
  **not** settled (``error_skipped`` receipts); replay of a settled nonce
  is rejected server-side; enabled-without-``payTo`` fails closed with 503.
- **Account path stays free**: a valid engine API key (``X-API-Key`` or
  Bearer) bypasses the paywall and remains key/quota-metered — anonymous
  agents pay per call, known users don't.
- **Receipts feed MeshPay**: canonical receipt hashes (same JS-faithful
  serialization discipline) and ``GET /x402/receipts/root`` returning the
  Merkle root MeshPay anchors.
- **Routes**: ``GET /x402/{config,receipts,receipts/root}``.
- **Live-verified**: 402 with decodable header → paid ``/audit/quick``
  streamed 9 SSE events and settled (receipt ``settled``, mocktx hash) →
  root endpoint returned the Merkle root; ``/dcm/infer`` 402 without
  payment and 502 (upstream offline) with an API key, proving the bypass.
- **Tests (+25)**: wire shapes and header codec, mock/HTTP facilitator
  (including error mapping), dependency→middleware flow (402 shape, invalid
  reason, buffered header, stream settle-after-body, replay, bypass, fail
  closed), receipts root vs Merkle recomputation, engine wiring for both
  audit routes and DCM infer. Documents in ``docs/X402.md``.

### Added — E1: Remote MCP (Streamable HTTP + token auth + Server Card)

The 28-tool MCP surface is now reachable over **Streamable HTTP**, the
transport 55% of registry servers use and the one the 2026 MCP roadmap
builds on (SSE deprecated), instead of stdio-only:

- **`backend/mcp_http.py`** — authed MCP endpoint at `POST /mcp/`, public
  `/health` and `/.well-known/mcp-server-card.json`. Raw-ASGI auth gate
  (MCP streams responses, so no body buffering) accepting
  `Authorization: Bearer <engine API key>` or `X-API-Key`; static
  `JAMBU_MCP_TOKEN` also supported. Unauthorized requests get 401 with a
  `WWW-Authenticate: Bearer` challenge and a message naming the credential
  to send — the registry census found thousands of unauthenticated remote
  MCP servers; this cannot be deployed as one. No CORS: server-to-server
  surface, not browser-facing.
- **Tool profiles** — `JAMBU_MCP_PROFILE=curated` drops `execute_tool`
  (arbitrary execution) and keeps the surface compact; default `full`.
- **Server Card** — honest discovery payload (transports, auth method and
  key source, active profile, tool names, no secrets), per the 2026
  Server Card direction.
- **Engine integration** — mounted at `/mcp` on the existing port with the
  engine's middleware stack; `/mcp` exempted from the 30s request timeout
  (audits and mesh inference legitimately run for minutes); the engine
  lifespan runs the MCP session manager (Starlette does not run lifespans
  of mounted sub-apps) and `reset_session_manager()` exists because the
  SDK's session manager is single-run per instance.
- **Registry ready** — `server.json` (schema-shaped, structurally tested)
  plus `docs/MCP_REMOTE.md`. Publishing is deliberately deferred until an
  HTTPS endpoint and namespace verification exist — documented rather than
  faked with a placeholder.
- **Live-verified**: official MCP client over HTTP → Bearer auth → 28
  tools listed → `check_engine_health` returned real engine data
  (RAM/CPU) through the engine REST loop; bearer-less requests rejected.
- **Tests (+16)**: card shape/no-secrets/URL fallback, static-token and
  DB-API-key validation, auth boundary (401 challenge, wrong token, bearer
  and X-API-Key acceptance, public paths), curated/full profile reload,
  manifest shape, engine mount card + auth, and a protocol E2E that spawns
  the server and drives it with the official `streamablehttp_client`.

### Added — MeshPay: USDC settlement for mesh compute (verifiable receipt auditing + Solana anchoring)

The DCM mesh meters every billable operation into a hash-chained
``settlementLog``. MeshPay makes that ledger auditable and payable:

- **Independent verifier** (`backend/modules/meshpay/`) — a from-scratch
  replay of DCM's chain, including `jsjson.py`, an ECMAScript-faithful
  JSON serializer (DCM hashes with `JSON.stringify`; its inference rates
  are `1e-5`/`5e-5`, exactly where Python's number formatting diverges).
  Pinned against real Node.js output on 34 edge cases; live cross-checked
  three ways (stored hash = Node.js recomputation = Python verifier) on
  real receipts containing floats like `1.2090960000000002`.
- **Epochs + payouts** (`plan.py`) — contiguous receipt windows, Merkle
  roots (canonical spec: domain-separated SHA-256, odd-node promotion),
  per-provider USDC payout plans with an explicit protocol fee. The
  DCT→USD rate is **configured, not an oracle**, and every USD figure says
  so.
- **Anchoring** (`anchor.py`) — Solana **memo-program** transactions
  (`meshpay:v1:<epoch>:<receipts>:<root>`, no custom program to deploy)
  over JSON-RPC, plus an explicitly-labelled offline `mock` transport
  (default). Missing toolchain/keypair/key fail loudly — nothing ever
  claims a chain transaction that did not happen. Anchor records persist
  `epoch_size` so re-verification regroups receipts identically
  (live-found bug: custom-size anchors showed a false "unavailable").
- **Routes** — `GET /meshpay/{config,audit,anchors,receipts/{i}}`,
  `POST /meshpay/anchor`. Audit reports our verdict, DCM's own verdict,
  and whether they agree. Inclusion proofs let one receipt be checked
  against an anchored root.
- **MCP tools (28 total)** — `meshpay_audit`, `meshpay_anchor`.
- **UI** — `MeshPayPanel` (chain verdict + disagreement warning, epochs
  with per-epoch anchor buttons, payout table, anchor history with status
  badges and explorer links) and `DcmNodePanel` (node readiness incl. the
  MoE-sidecar case, model catalog, LAN join URLs, one-shot infer box);
  both registered in the sidebar, command palette, and canvas router.
- **Tests (+91)** — serialization table, chain verify (tamper/link/
  truncation/anonymous-charge), Merkle proofs for 1–8 leaves, payout math,
  anchor transport behavior (mock determinism, refusal to downgrade,
  offline transaction construction with `solders`), config, 14 route tests
  including the epoch-size regression, plus 17 frontend tests.
- **Live-verified**: 4 real receipts generated via DCM's billing API →
  independent verification + DCM agreement → epoch roots → mock anchor
  persisted → re-verification `verified` → inclusion proof verified → MCP
  audit through the engine. Devnet anchoring is a documented runbook
  (`docs/MESHPAY.md`) blocked only on a funded keypair.
- `solders` declared in requirements (optional at runtime); runbook and
  honest "not done yet" list (USDC payouts, paging, forward markets,
  node→wallet binding) in `docs/MESHPAY.md`.

### Added — DecentraCode Mesh (DCM) integration, phase 2: engine routes + MCP tools

The mesh is now reachable from the app and from agents, not just the CLI:

- **Engine routes** (`backend/routes/dcm.py`, `/dcm/*`) — `status`,
  `models`, `join-info`, `earnings/{did}`, `token/balance/{did}`,
  `settlement-log?limit=`, and `infer` (non-streaming). All 502s instead
  of hanging: unreachable nodes get "start it with `cd
  decentracode/backend && npm start`", runtime failures carry DCM's own
  explanation, auth failures point at `JAMBU_LLM_DCM_AUTH`.
  `/dcm/` is excluded from the 30s request timeout (mesh inference is
  slow by design — 2–5 tok/s on the MoE path).
- **MCP tools (26 total, +5)** — `dcm_status`, `dcm_infer`, `dcm_models`,
  `dcm_earnings`, `dcm_settlement_log`. Any MCP client (Claude, Cursor)
  can now operate and verify a DCM node. `docs/MCP_TOOLS.md` regenerated.
- **`_call_engine` surfaces non-200 detail** — every MCP tool that hits an
  engine 502 now reports the engine's own explanation instead of a bare
  status code (this is what makes `dcm_infer`'s missing-runtime message
  actionable through the agent path). All 26 tools benefit.
- **Live-verified end to end** against a real node: MCP tool →
  `/dcm/*` route → DCM. Status correctly reports `python-moe ready
  (candle-dense: Binary not found: …)`, models `2/16 available`,
  settlement log reads DCM's nested `verification` block
  (`chain valid: True`, totals). Two formatter bugs were found this way
  and fixed (the `error` key masking a ready secondary runtime; nested
  verification ignored).
- **Tests (+22)**: `tests/test_dcm_routes.py` — 11 route tests (payloads,
  limit validation, 502 mappings, infer validation/error mapping), 3 MCP
  registration/schema tests, 4 formatter/`_call_engine` regressions; the
  MCP expected-tool snapshot now pins all 26 tools.

### Added — DecentraCode Mesh (DCM) integration, phase 1

Jambubrowser can now use a local **DecentraCode Mesh** node as a
first-class LLM provider and operate it from the CLI. Live-verified
against a real DCM backend (`npm start` on this machine):

- **`dcm` LLM provider** (`backend/llm/providers/dcm.py`) — talks to the
  mesh's OpenAI-compatible endpoint
  (`POST {node}/api/inference/v1/chat/completions`). Parsing is pinned to
  DCM's real wire format: non-streaming `chat.completion` JSON, plus its
  native SSE frames (`{"token", "text", "finished"}` per token; terminal
  `{finished, outputText, outputTokens, perStepMs, tokPerSec}`), with
  OpenAI-style deltas accepted for forward compatibility. Error mapping
  is actionable: 401→auth (with a DID-Sig hint), 404→"not a DCM node",
  and missing runtimes (501 `RUNTIME_NOT_IMPLEMENTED` or 500 `ENOENT`
  spawn failures — both observed live) → "build backend/p2pd binaries or
  start the dense coordinator". DCM is treated as a **local provider**
  (zero USD cost; DCT metering is mesh-side) and is allowed in
  local-only privacy mode. Enable with `JAMBU_LLM_PROVIDER=dcm` (not in
  the default fallback chain — opt-in).
- **`DcmClient`** (`backend/modules/dcm_client.py`) — async REST client
  for node/mesh/billing state: `/api/inference/status`, `/api/models`,
  `/api/network/{status,peers,join-info}`, `/api/billing/earnings/:did`,
  `/api/billing/settlement-log`, `/api/token/balance/:did`, plus a
  `summary()` for CLI/UI. Live-contract correction baked in: DCM answers
  **503 with a state body** on `inference/status` when the default
  runtime is missing while a secondary (MoE sidecar) is ready.
- **Provider health** reflects runtime readiness, not just HTTP 200:
  healthy when any runtime (top-level or nested `moe`) is ready.
  Live-verified claim shape: candle binary missing + MoE sidecar ready →
  provider healthy, `jambu dcm status` prints
  `python-moe ready (candle-dense: Binary not found: …)`.
- **CLI**: `jambu dcm status` (node/inference/models/mesh overview) and
  `jambu dcm infer "<prompt>" [--model M] [--max-tokens N]` — talks to
  the node directly via `JAMBU_DCM_URL` (default `127.0.0.1:3001`), no
  engine required. Missing-runtime failures exit 2 with the node's own
  error text.
- **Tests (25 new)** — `tests/test_dcm_provider.py`: both stream frame
  dialects, snake_case terminals, error frames, 501/ENOENT/401/404
  mapping, auth header forwarding, zero-cost accounting, registry
  discovery + local-only mode, and client tests for models/earnings/
  settlement-log/errors/summary. `tests/test_dcm_cli` additions (6):
  status (ready + MoE-sidecar shapes), unreachable exit 2, infer output,
  runtime-missing exit 2. `tests/test_dcm_integration.py` (6) runs
  against a live node when one answers `/health` (via
  `JAMBU_TEST_DCM_URL`), including a contract check that needs no
  runtime (empty messages → 400 `INVALID_MESSAGES`) and generation tests
  that **skip with DCM's own reason** when the model artifacts are
  absent.
- **Also fixed while validating against the live node**: `has()` in the
  provider registry now triggers discovery (auto-mode chains could skip
  undiscovered providers), and `DCMProvider.health()` no longer treats a
  not-ready default runtime as a dead provider.
- **Live state (honest)**: node contract, models catalog (16 models,
  2 available), CLI, client and provider error paths verified live.
  Real token streaming is blocked on model artifacts on this machine —
  the MoE path needs `mlx_lm` + ~9 GB HF weights
  (`gdax/Qwen1.5-MoE-A2.7B_gguf`), the candle path needs the Rust
  binary (`cargo build` in `backend/p2pd`). The integration tests prove
  the pipeline the moment either exists.

### Fixed — five bugs found by validating the public use-case claims

A claim-by-claim audit of the product's use cases (live engine, real
Playwright, mock LLM) turned up five defects, four in surfaces the
marketing copy already claimed working:

- **The tamper-evident audit-log chain never verified.** `AuditLogger.log`
  hashed one `time.time()` value but stored a second, different one, so
  `/audit/verify` reported "Chain broken at entry 1" for every database
  ever written — the "tamper-evident" claim was false in practice. The
  test that should have caught it asserted only that the return value was
  a bool. `log()` now uses a single timestamp; the test asserts
  `is_valid is True` and a new test proves tampering *is* detected.
  Note: rows written before this fix remain unverifiable (re-sealing an
  existing chain is deliberately out of scope — it would legitimise any
  prior tampering).
- **Missions could be created but never seen or run** (four separate
  defects in one feature):
  - `POST /mission` wrote directly to the DB while `GET /mission/list`
    read the scheduler's in-memory store, which was never populated →
    UI-created missions vanished immediately (MissionsPanel calls both).
  - `POST /mission/start-scheduler` called `MissionScheduler.start()`,
    which did not exist → HTTP 500.
  - No research handler was ever registered, so any due mission failed
    with status `error`.
  - Run state (`last_run`) was never written back, so every reload
    (`load_from_db`) made a just-run mission due again — it re-executed
    on each scheduler tick.
  Fixes: routes persist and list through one store (DB sync on list/stop),
  `start()`/`stop()` implemented (idempotent loop + cancellation), the
  engine lifespan registers a real research handler
  (`routes.research._brain_only_research`), and `_persist_mission_state`
  writes `status`/`last_run`/`next_run` back after every run.
- **Procedural-memory outcome endpoint 500s** on an unknown/missing `id`
  (`ValueError` escaped as a 500). Now 404 with a clear message.
- **Test isolation**: `reset_memory()` (which existed but was unused) is
  now wired into the autouse isolation fixture — a stale memory store
  pointed at a closed per-file DB caused late-suite "no such table:
  procedural_memory" 500s.
- **Tests added (12)**: full mission lifecycle (`tests/test_missions.py` —
  create→list round-trip, stop, cross-process listing, scheduler
  start/stop idempotency, execution + persistence, reload-doesn't-rerun,
  missing-handler error, DB-loaded missions), chain integrity + tamper
  detection, procedural 404s.

### Added — visual diff heatmaps (see *what* changed)

- The change percentage told you *how much* changed but not *where*.
  New `render_diff_image` (`backend/modules/visual_diff.py`) paints
  changed pixels pure red over a dimmed page (same tolerance rule as the
  percentage, so the heatmap and the number always agree; identical
  pairs render the dimmed page with no red). Wide captures are capped at
  640px for payload sanity; missing Pillow/bad inputs return `None`.
- `GET /audit/monitors/{id}/runs/{run_id}/diff` serves the heatmap PNG.
  The previous screenshot is the newest successful run strictly older
  than the run (tie-broken by id): 404 when the run is unknown, has no
  screenshot, or is the baseline; 500 when rendering fails.
- Monitors panel run rows link "diff" next to the thumbnail whenever a
  comparison exists (hidden for baselines); CLI:
  `jambu monitor diff <monitor-id> <run-id> [--out FILE]`.
- **Tests** — 8 render tests (red region placement, identical/no-red,
  dimming, jitter tolerance, resize, width cap, bad inputs, missing
  Pillow), 5 API tests (heatmap red-pixel assertion, baseline/no-shot/
  unknown 404s, cross-monitor scoping), 1 CLI test, 2 frontend tests.

### Added — viewable monitor screenshots

- Stored run screenshots were **write-only**: the percentage was shown
  everywhere but the image itself was unreachable. Now:
  - `GET /audit/monitors/{id}/runs/{run_id}/screenshot` serves the raw
    PNG (`image/png`). 404 when the monitor/run is unknown, belongs to a
    different monitor, or stored no screenshot; 500 when the stored
    payload isn't decodable image data (scoped getter
    `get_run_screenshot` prevents cross-monitor ID guessing).
  - Monitors panel run rows show a thumbnail (when `has_screenshot`) that
    opens the full PNG in a new tab; no thumbnail when the run stored
    none.
  - CLI: `jambu monitor screenshot <monitor-id> <run-id> [--out FILE]`
    downloads the PNG (default `monitor-<id>-run-<rid>.png`).
- **Tests** — 7 API tests (PNG round-trip bytes, unknown monitor/run,
  missing screenshot, cross-monitor scoping, corrupt-data 500, getter
  scoping), 2 CLI tests (writes bytes, engine-error exit 2), 3 frontend
  tests (thumbnail src/href, omission without screenshot, URL helper).

### Added — visual regression detection for audit monitors

- **Screenshot diffing** (`backend/modules/visual_diff.py`) — monitors now
  store each run's screenshot and compare it with the previous run's:
  per-pixel max-channel delta on a downscaled RGB copy, with a tolerance
  band that absorbs anti-aliasing/rendering jitter. Identical images take
  a hash fast-path (0.0%); results are a change percentage. Pillow is a
  declared dependency; if it's missing the diff degrades to `null`
  instead of failing the run.
- **Visual alerts** — monitors gained `visual_threshold_pct` (default
  2.0; `0` disables alerts but still records the percentage). Exceeding
  the threshold sends a desktop notification and a webhook event
  (`audit.visual_change`) with the percentage; the findings webhook
  (`audit.regression`) is unchanged.
- **Storage + retention** — `screenshot_b64` and `visual_change_pct` are
  persisted per run; only the newest 20 runs per monitor are retained so
  screenshots don't grow the DB unbounded. Column migrations handle
  databases created before this existed.
- **Surfaced everywhere** — run history (API, CLI `monitor runs`, and the
  Monitors panel) shows `visual X.XX%`; the run-now response includes
  `visual_change_pct` / `visual_changed` / `visual_alerted`.
- **Pipeline plumbing** — `_audit_event_stream(req, on_collected=...)`
  exposes the collected `AuditData` to callers without bloating the SSE
  payload; monitors use it to grab the screenshot.
- **Fixed — useless monitor errors**: `_execute_audit` swallowed the
  pipeline's `error` event and raised "audit did not produce a done
  event". It now includes the failing phase and cause (e.g. the missing
  Playwright browser that exposed this).
- **Tests** — 11 diffing tests (identical / 100% / partial / jitter
  tolerance / resize / decode failure / missing Pillow / hash), 7 monitor
  visual tests (baseline, changed, unchanged, disabled threshold,
  missing screenshot, webhook payload, pruning), 4 API validation tests,
  and an error-surface regression test.

### Added — HTML reports, share links, and the missing audit-history UI

- **HTML report exporter** (`findings_to_html` in
  `backend/employees/export.py`) — self-contained (inline CSS, no
  external assets), print-friendly (browser → Save as PDF), severity
  stats + grouped findings with fixes/evidence/WCAG/impact. Every field
  is `html.escape`d: findings are LLM prose about arbitrary pages, so the
  shared report is a stored-XSS surface if anything slips through.
- **Routes** — `GET /audit/report/{audit_id}` and
  `GET /audit/shared/{token}/report` render the same page for local
  history and public share links.
- **`done` event now carries `audit_id`** — the pipeline persists the
  audit *before* announcing completion so the UI's Report / Share /
  Export actions can target the saved row.
- **AuditPanel history UI** — the backend history/export/share endpoints
  were fully built but had **no UI surface at all**:
  - Recent audits list (expandable) with per-row Report, Share, and
    SARIF download actions.
  - Run banner actions: Report (modal with sandboxed `srcDoc` iframe),
    Share (creates a link, copies to clipboard, shows the URL), and an
    Export dropdown (SARIF / canonical JSON / Markdown downloads).
  - Share-link strip shown for shares from either place.
- **CLI** — `jambu report <audit-id> [--out FILE|-]` downloads the HTML
  report; `jambu share` now prints both the JSON and HTML report URLs.
- **Fixed — history dates rendered as 1970**: `audit_history.created_at`
  used SQLite `julianday('now')` (~2.46e6) while clients treated it as
  epoch seconds. New rows store epoch seconds; reads normalise legacy
  Julian Day values transparently (`_created_at_epoch`), and the test
  suite pins both formats.
- **Tests** — exporter (incl. hostile-content escaping), routes (report,
  shared report, 404s), the full pipeline end-to-end without Playwright
  (`tests/test_audit_pipeline.py`: done carries `audit_id`, history is
  post-dismissal, report renders pipeline output), history timestamp
  normalisation, 6 AuditPanel component tests, 4 CLI tests.
- **Fixed — test isolation**: the cached `AuditLogger` created its table
  against whichever database was current at first use, so suites that
  redirect `JAMBU_DB_PATH` per file could poison later tests with
  "no such table: audit_log". Added `reset_audit_logger()` and reset it
  in the autouse isolation fixture.

### Added — continuous audit monitors (regression alerting)

- **Recurring audit monitors** — `POST /audit/monitors` schedules the audit
  pipeline on an interval and diffs each run's active findings against the
  previous run: new / resolved / persisting. New findings at or above the
  monitor's `fail_on` severity trigger a desktop notification and an
  optional webhook POST (Slack-compatible JSON). The first run is a
  baseline and never alerts.
  (`backend/modules/audit_monitor.py`, `backend/routes/audit_monitors.py`)
- **Scheduler** — started from the engine lifespan, ticks every 60 s and
  runs due monitors; `POST /audit/monitors/check-now` triggers a due-check
  manually. Monitor state (last run, status, finding count) is persisted.
- **Shared pipeline, no drift** — the audit SSE endpoints and the scheduler
  now run the same `_audit_event_stream` generator
  (`backend/routes/audit.py`), so dedupe, dismissal filtering, and history
  persistence are identical for interactive and scheduled runs.
- **CLI** — `jambu monitor add|list|rm|run|runs` with `--interval`,
  `--fail-on`, `--webhook`, `--run-now`. Run history marks baseline runs
  and shows `+new` / `-resolved` deltas.
- **UI** — Monitors panel (`browser-app/src/components/monitors/`): create
  form (interval, threshold, webhook, "baseline now"), per-monitor
  enable/disable, run-now with inline diff result, expandable run history
  with baseline / `+new` / `−resolved` badges, delete. Registered in the
  sidebar, command palette (⌘K), and lazy canvas switch.
- **Tables** — `audit_monitors` + `audit_monitor_runs` (per-run
  fingerprints, diffs, baseline flag, errors).
- Also fixed: `jambu diff` / `jambu monitor runs` silently ignored their
  `limit` parameter (GET query params were sent as a request body).

### Added — CI audit pipeline (audit wedge productization)

- `jambu audit|quick --sarif FILE --json FILE --markdown FILE` — the CLI can
  now emit all three export formats (SARIF 2.1.0 for GitHub code scanning,
  canonical JSON for dashboards, Markdown for humans). `-` writes to stdout
  for piping. (`cli/jambu.py`)
- `jambu ... --fail-on critical|high|medium|low|none` — severity gate with a
  stable CI contract: **0 = pass, 1 = gate failed, 2 = engine error**.
- The CLI consumes the engine's post-dedup, post-dismissal findings from the
  `done` event, so dismissals survive into CI and known false-positives
  never re-fail a build.
- Root `action.yml` — composite GitHub Action: installs the engine + Chromium,
  starts it, runs the audit, uploads SARIF to code scanning, and enforces the
  gate (`continue-on-error` on the audit step so SARIF uploads even when the
  gate fails). Supports `engine-url` to skip installation entirely.
- `examples/github-actions/jambu-audit.yml` and `docs/CI.md` — copy-paste
  workflow, input/output tables, exit-code contract, cost notes, and
  non-GitHub CI recipes.
- **Playwright was undeclared**: the audit engine imports it, but it was
  missing from `requirements.txt` and `pyproject.toml` — a fresh install
  couldn't run the headline feature. Added, with the
  `python -m playwright install chromium` step documented in the README.
- `pyproject.toml` packaging switched to `packages.find` (`backend*`, `cli*`)
  so non-editable installs include subpackages like `backend.employees` —
  the CLI's export imports would previously break in a wheel install.

### Fixed — trust reset (dead endpoints, browser identity, honest claims)

**Desktop browser tab identity (the pane was effectively non-functional)**
- The Rust engine assigns tab IDs (`tab-xxxxxxxx`) at creation, but the
  frontend discarded them and generated `crypto.randomUUID()` IDs — every
  `invoke()` referenced a tab the engine had never seen (`Tab not found`),
  and the viewport stayed empty. Tabs are now reconciled on `browser-ready`
  and `browser-restarted`: the store's desired tabs are recreated in the
  engine, engine IDs are adopted verbatim, and closing the last tab creates
  the engine replacement first so store and engine never diverge.
  (`browser-app/src/components/browser/ChromiumPane.tsx`,
  `browser-app/src/store/appStore.ts` — `syncEngineTabs`)
- The pane now probes engine readiness on mount: `browser-ready` fires
  once per engine start, so switching canvas tabs away and back previously
  left the pane stuck on "Starting Chromium engine..." forever.

**SSE streaming in the Tauri build (appeared frozen until completion)**
- `proxy_localhost` buffered the whole response body, so agent/audit event
  streams arrived only after the run finished. New `proxy_stream` Rust
  command forwards response chunks over a Tauri IPC channel
  (`Response::chunk()`, base64-framed); `localFetchStream()` wraps them in a
  real `ReadableStream`, and aborting the caller's signal cancels the Rust
  task via `proxy_stream_cancel`. `runAgentStream` and `AuditPanel` use it.
  (`browser-app/src-tauri/src/commands/stream.rs`,
  `browser-app/src/utils/api.ts`)

**Native menu did nothing**
- Menu items emitted `menu-event` with no listener. All items are now wired
  (New/Close Tab, Reload, Back/Forward, Find, Bookmark Page/All Tabs,
  toggle bookmark bar, DevTools, Next/Prev Tab, Full History) with a
  timestamp guard so menu accelerators and the JS shortcut handler never
  double-fire. Dead "New Window" and zoom items were removed rather than
  shipped non-functional; Next/Prev Tab accelerators moved to
  Ctrl+Tab / Ctrl+Shift+Tab (Cmd+Tab is owned by macOS).

**~25 endpoints were dead at runtime (import mismatches, silent 500s)**
- `backend/modules/vision.py` — added the module-level API the `/vision/*`
  routes import (`analyze_image`, `ocr_image`, `detect_ui_elements`,
  `verify_screen`).
- `backend/modules/computer.py` — new: Quartz-backed `mouse_action()` and
  `press_key()` for `/computer/*`; the keyboard route's `key=` branch no
  longer discards the key code.
- `backend/routes/models.py` — all 6 `/mlx/*` endpoints now call the real
  `mlx_provider` functions (`get_provider_info`, `mlx_start_server`,
  `mlx_stop_server`, `mlx_generate`, `mlx_download_model`).
- `backend/modules/local_connector.py` — added async module-level wrappers
  for `/local/obsidian/*` and `/local/reminders/create`.
- `backend/modules/youtube.py` — added `analyze_youtube`,
  `get_youtube_transcript`, `search_youtube_transcript` wrappers.
- `backend/modules/multimodal_input.py` — added `process_image` /
  `process_file`; file processing is confined to `~/.jambu/uploads`
  (path-traversal rejected, 400/404 mapped in the route).
- `backend/routes/missions.py` — `/notifications/*` now use `get_notifier`
  and map `level` onto `Urgency`.
- `backend/agent/builtin_tools.py` — `risk_check` used a nonexistent
  `get_risk_shield`; now `get_shield().assess_url()` with the real result
  shape.
- `backend/plugins/manager.py` — `LATEST_LLM_CONFIG` imported from the
  module that defines it (`engine_runtime`).
- `backend/routes/vault.py` — missing `import os` (latent 500 when locked).
- `tests/test_import_contracts.py` — new static contract test: every
  `from backend.* import Name` in the codebase must resolve. This is the
  guard that would have caught the whole class of bug.

**Procedural memory was written but never read**
- `backend/memory/retrieval.py` imported a module-level `list_procedural`
  that never existed, and the agent loop swallowed the error. It also
  called `success_rate` as a property instead of the `success_rate()`
  method. Both fixed; new tests assert stored procedural memory reaches
  the planner context. (`tests/test_memory_system.py`,
  `tests/test_agent_loop.py`)

**Frontend quick-scan button hit a missing route**
- `POST /audit/quick` was documented and called by `AuditPanel` but never
  registered. Added as a thin alias of `/audit/run` with mode forced to
  `quick`; regression test asserts the route exists in OpenAPI.

**Audit dismissals are now applied server-side**
- The dismiss/undismiss API existed but only the UI filtered dismissed
  findings. `/audit/run` and `/audit/quick` now partition findings into
  active + suppressed (by content fingerprint, scoped per URL); the `done`
  SSE event carries `dismissed_count` + `dismissed` metadata, and history
  / exports persist only active findings. The route-level hash and the
  canonical export's `content_hash` are unified into
  `employees.export.content_fingerprint` (one source of truth).

**Test-environment robustness**
- `tests/test_mcp_server.py` live e2e now verifies the service on :8001
  identifies as Jambubrowser before asserting, so an unrelated local
  service on the same port skips instead of failing.

**Audit pipeline honored the wrong LLM provider**
- `ProductContextExtractor` hardcoded `provider="minimax"`, ignoring
  `JAMBU_LLM_PROVIDER`/the fallback chain — it leaked calls to an
  unintended service, failed on rate limits, and broke offline/CI runs.
  It now uses the registry's configured default like every other
  employee; regression test in `tests/test_employees.py`. Verified live:
  a full `jambu quick` run with `JAMBU_LLM_PROVIDER=mock` produces zero
  external calls and a valid SARIF file.

### Verification
- Backend: 874 passed, 3 skipped (CI batch).
- Frontend: 342 vitest tests, 0 lint errors; `cargo check` clean.

## [3.3.0] - 2026-06-13

### Added — Security hardening & middleware stack

A complete security middleware layer wrapping the FastAPI app, plus
Pydantic field validation on the highest-impact endpoints. This
release closes every P2 item from the most recent improvement audit.

**Security middleware (`backend/core/`)**
- `security.py` — input-validation primitives: `is_safe_url()` (SSRF
  protection with private-IP blocking), `safe_filename()` (path
  traversal), `is_safe_path()` (canonical-path containment check),
  `sanitize_html()` (XSS-prevention regex), `validate_file_upload()`
- `security_headers.py` — `SecurityHeadersMiddleware`. Adds
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, `Content-Security-Policy`, `Cross-Origin-Opener-Policy`,
  `Cross-Origin-Resource-Policy` to every response. Adds
  `Strict-Transport-Security` on HTTPS requests.
- `body_size_limit.py` — `BodySizeLimitMiddleware`. Rejects requests
  with bodies larger than 2 MB (HTTP 413). Checks `Content-Length` and
  streams to enforce on chunked encoding.
- `trusted_host.py` — `TrustedHostMiddleware`. Rejects requests with
  untrusted `Host` headers (HTTP 421). Protects against DNS rebinding
  and Host header injection. `ALLOWED_HOSTS` env var for configuration.
- `request_id.py` — `RequestIDMiddleware`. 12-char hex correlation ID
  per request, reusable from `X-Request-ID` header. Stored on
  `scope["request_id"]` for downstream handlers.
- `request_timeout.py` — `RequestTimeoutMiddleware`. Cancels requests
  exceeding 30 s (HTTP 504). Excludes long-running endpoints via
  `exclude_paths` (`/research`, `/scrape`, `/v2/`, `/mlx/`, etc.).
- `access_log.py` — `AccessLogMiddleware`. One structured log line per
  HTTP request: method, path, status, duration_ms, ip, rid, OK/SLOW.
  Skips `/health` to keep logs clean.
- `security_events.py` — `log_security_event()` helper. Wraps the
  tamper-evident audit log; integrated into 4 middlewares
  (rate_limiter, body_size_limit, trusted_host, request_timeout) so
  blocked requests are recorded for forensic review.

**SSRF protection**
- `is_safe_url()` validators added to all URL-accepting Pydantic models
  and query-param endpoints across 9 route files
  (`research.py`, `browser.py`, `tools.py`, `missions.py`,
  `harness.py`, `media.py`, `knowledge.py`, `vault.py`, `system.py`).
- Default blocks localhost, RFC1918, link-local, loopback, ULA.
  `allow_private=True` for dev/SSRF-into-self.

**Command injection fix**
- `/computer/keyboard` (`backend/routes/browser.py`) — escape
  double quotes in the osascript `keystroke` argument so user-typed
  text cannot break out of the AppleScript string.

**Engine decomposition**
- `backend/engine.py` reduced from ~4200 lines to 254 lines.
- 176 route handlers extracted into 20 domain-specific modules under
  `backend/routes/`: `research`, `browser`, `vault`, `knowledge`,
  `memory`, `local`, `missions`, `tools`, `models`, `p2p`, `goals`,
  `consensus`, `harness`, `v1`, `v2`, `multimodal`, `fingerprint`,
  `media`, `system`, `ws`.
- `backend/engine_runtime.py` — shared `ConnectionManager`, `safe_task`,
  broadcast helpers, LLM config resolution.

**WebSocket hardening**
- `client_id` validation: regex `[A-Za-z0-9_\-:.]{1,64}` blocks
  path-traversal, special chars, oversized strings.
- Per-IP connection cap (default 8, env: `WS_MAX_CONNECTIONS_PER_IP`).
- Global connection cap (default 256, env: `WS_MAX_TOTAL_CONNECTIONS`).
- Reconnect cleanly closes stale sockets (HTTP 1008 close code).
- `manager.get_stats()` for observability.

**Error response sanitization**
- `str(exc)` hidden in production (controlled by `JAMBU_DEBUG`).
- `request_id` added to all error responses for correlation.
- `runtime_check` on `JAMBU_DEBUG` so tests can toggle without
  module reload.

**CORS + compression**
- `Access-Control-Max-Age=3600` for CORS preflight caching.
- `GZipMiddleware(minimum_size=500)` for response compression.

**Input validation**
- `ExecRequest` — timeout clamped to `[1, 120]` s, code ≤ 50 000 chars.
- `ResearchRequest` — query 1-10 000 chars, `top_n` ∈ `[1, 50]`,
  `domain` ∈ `{general, academic, coding}`.

**Audit PII redaction refactor**
- `backend/core/audit.py._redact_pii` now uses the shared
  `PIIDetector` (10 PII types vs 3), recurses into lists, and fully
  redacts known secret keys (`password`, `api_key`, `token`, etc.)
  instead of pattern-matching their values. Preserves surrounding
  text rather than blanking the whole field.

**Calculator hardening (`tools/calculator.py`)**
- Reject booleans (`True`/`False` were sneaking through as `int`).
- Catch `ZeroDivisionError` and `OverflowError` and return error dict.

**/health dependency probes**
- Endpoint now actively probes DB, audit log, and credential vault.
  Returns `degraded` if any critical dependency is unreachable.
  Includes a `checks` dict for monitoring tooling.

**Logging & type safety**
- 31 `print()` calls replaced with structured logging across 13 files.
- Return type hints added to `ConnectionManager`, lifespan handlers,
  `safe_task`.

**MCP server (`tools/mcp/`)**
- Exposes all Jambubrowser features as MCP tools for AI assistants.
  stdio + HTTP/SSE transports.

**Tests — 336 passed at the time of this release.** The suite has grown
substantially since; run `python3 -m pytest tests/` for the current
pass/fail count (see README → Testing) instead of relying on any number
recorded here.
| File | Tests | Coverage |
|---|---|---|
| `test_core_security.py` | 41 | `is_safe_url`, `safe_filename`, etc. |
| `test_engine_runtime.py` | 37 | `ConnectionManager` security (15 new) |
| `test_security_headers.py` | 11 | incl. 4 HSTS |
| `test_body_size_limit.py` | 9 | |
| `test_trusted_host.py` | 17 | |
| `test_request_id.py` | 11 | |
| `test_error_sanitization.py` | 9 | |
| `test_request_timeout.py` | 9 | |
| `test_security_events.py` | 10 | |
| `test_access_log.py` | 10 | |
| `test_calculator.py` | 52 | arithmetic, security, edge cases |
| `test_audit_redaction.py` | 15 | new PIIDetector integration |
| `test_health_endpoint.py` | 4 | online/degraded paths |
| `test_privacy.py` | 28 | `PIIDetector`, `NetworkIsolator`, etc. |
| `test_supply_chain.py` | 14 | `SupplyChainVerifier` |
| `test_exec_request.py` | 16 | `ExecRequest`, `ResearchRequest` |
| **New in this release** | **291** | |

**Docker**
- `Dockerfile` + `.dockerignore`
- `docker-compose.yml` updated

**Frontend**
- `/v2` proxy added to `vite.config.ts`

## [3.2.0] - 2026-06-11

### Added — Evaluation harness

`backend/eval/` — a lightweight benchmark framework for measuring research-agent
quality across LLM providers. Inspired by GAIA and WebArena but built as
small, self-contained suites that run in minutes.

**Core** (`backend/eval/`)
- `harness.py` — `Task`, `TaskResult`, `SuiteResult`, `Harness` with `run_task` + `run_suite` + `compare_providers`
- `metrics.py` — `exact_match`, `contains_match`, `fuzzy_match`, `number_match`, `email_redaction_match`
- `store.py` — SQLite-backed results storage (reuses `backend.core.database`)
- `report.py` — Markdown + JSON + comparison reports
- `cli.py` — `python -m backend.eval {list,run,compare,report}`
- `__main__.py` — `python -m backend.eval ...` invocation

**Task suites** (`backend/eval/tasks/`)
- `smoke.py` — 5 fast sanity tasks (~30s)
- `gaia_mini.py` — 10 reasoning tasks (capital, arithmetic, multihop, common sense, logic, dates, math, causal, reading, conversion)
- `webarena_mini.py` — 8 browser-based tasks using the agent loop
- `privacy.py` — 7 PII redaction + prompt-injection resistance tasks
- `memory.py` — 5 memory-layer tasks (recall, procedural, store+recall, context, forgetting)

**35 eval tests pass** (`tests/test_eval.py`)

**Usage:**
```bash
python -m backend.eval list                              # show all tasks
python -m backend.eval run --suite smoke --provider mock # run smoke
python -m backend.eval compare --suite smoke --providers mock,ollama,anthropic
python -m backend.eval report --limit 20                 # past runs
```

See `docs/EVAL.md` for the full API.

## [3.1.0] - 2026-06-11

### Added — Tauri shippable build

**Tauri 2 desktop wrapper (`browser-app/`) is now production-ready:**
- `tauri.conf.json` hardened: real Content-Security-Policy, 1280x800 default window with min-size, proper bundle metadata (category, copyright, publisher, short/long description, homepage), Linux deb/rpm/appimage targets with proper deps
- `Info.plist` with bundle metadata, macOS permission descriptions, deep-link URL scheme (`jambubrowser://`), per-folder usage strings, environment variables for autostart
- `entitlements.plist` with hardened-runtime settings, network client/server, sidecar execution, file access scopes (sandboxed)
- `Cargo.toml` adds `tauri-plugin-updater`, `tauri-plugin-notification`, `tauri-plugin-deep-link`, `tauri-plugin-process`; release profile tuned for size (LTO, opt-level "s", strip)
- `lib.rs` registers all 5 plugins, uses `env_logger`, spawns backend services on a non-blocking thread
- `capabilities/default.json` updated with: window/webview/event/menu/tray defaults, `shell:allow-spawn` for Python + llama-server, `shell:allow-open` for browser navigation, scoped `shell:allow-execute` for sidecars
- Auto-updater configured for GitHub Releases endpoint with `createUpdaterArtifacts: true`

**Build pipeline (`scripts/`):**
- `dev.sh` — one-command dev mode: starts backend, auto-detects MLX (Apple Silicon) or Ollama, then Tauri. Color-coded logs to `/tmp/jambu-*.log`. Flags: `--no-llm`, `--no-backend`
- `build.sh` — production build with optional code signing. Auto-detects host platform, supports `--target <triple>`, `--skip-signing`, `--debug`
- `sign.sh` — standalone macOS signing + notarization helper. Signs inner binaries (deep, strict, runtime), verifies, then submits to `notarytool` and staples the ticket. Env-driven: `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`
- `gen-updater-keys.sh` — generates Tauri updater keypair (private + public). Refuses to overwrite existing keys. Outputs to `~/.tauri/jambu-updater.*`

**CI/CD (`.github/workflows/`):**
- `test.yml` — runs on every push: Python tests on Python 3.9/3.10/3.11/3.12, frontend build, integration tests, ruff lint, `tsc --noEmit`
- `release.yml` — runs on `v*` tags: 4-platform matrix (macOS aarch64, macOS x86_64, Linux, Windows), Apple Developer ID code signing + notarization, Tauri updater signing, draft GitHub Release with all installers
- `dependabot.yml` — weekly updates for pip, npm (frontend + tauri), cargo, and GitHub Actions

**Env hygiene:**
- `.env.example` documents every supported env var (LLM provider, signing, GitHub tokens, observability)
- `requirements.txt` at project root (was missing) with version-pinned deps
- `.gitignore` already covers `.env`, `*.key`, `node_modules`, build artifacts

**Frontend (browser-app/README.md) rewritten** with full Tauri documentation:
stack, architecture, prerequisites, dev/build/sign workflow, distribution, project structure, available plugins, custom URL scheme, troubleshooting

## [3.0.0] - 2026-06-11

### Added — The Three Pillars

This release consolidates Jambubrowser's LLM, agent, and memory subsystems into
production-grade modules. The codebase gains a unified provider abstraction, a
proper ReAct/Plan-Execute agent loop with verification, and a real memory &
personalization system.

**Pillar 1 — Unified LLM Provider Layer** (`backend/llm/`)
- `base.py` — `Provider` Protocol, `ChatMessage`, `Usage`, `ChatResponse`, `StreamChunk`
- `registry.py` — Singleton registry with auto-discovery + env-driven default
- `routing.py` — `Router` with `cheapest` / `fastest` / `quality` / `fallback` / `local_only` / `auto` strategies
- `config.py` — Env-based config: `JAMBU_LLM_PROVIDER`, `JAMBU_LLM_FALLBACK_CHAIN`, `JAMBU_LLM_TIMEOUT`, `JAMBU_LLM_LOCAL_ONLY`, per-provider model + base URL overrides
- `providers/anthropic.py` — Claude Opus / Sonnet / Haiku with proper system-prompt handling, tool use, streaming
- `providers/openai.py` — GPT-4o / GPT-4.1 / o1 / o3-mini with tool use, streaming
- `providers/ollama.py` — Native `/api/chat` + `/api/generate` fallback
- `providers/mlx.py` — Apple Silicon MLX VLM server wrapper
- `providers/minimax.py` — MiniMax cloud fallback
- `providers/mock.py` — Deterministic mock for tests + offline demos (supports tool-call mode)
- Cost estimation table covering all paid providers (per-1M-token pricing)

**Pillar 2 — ReAct / Plan-Execute Agent Loop** (`backend/agent/`)
- `loop.py` — `Agent` class with `run()` (async iterator over events) + `run_to_completion()`
- `plan.py` — LLM-driven goal decomposition, JSON parsing, replan on failure
- `tools.py` — `ToolSpec`, `ToolRegistry`, auto-derived JSON Schema from Python signatures, Anthropic + OpenAI tool format converters
- `verifier.py` — LLM-based "did this step advance the goal?" judge with heuristic fallbacks
- `events.py` — SSE event types: `run_started`, `plan_created`, `step_started`, `tool_called`, `tool_failed`, `step_verified`, `replanned`, `answer_ready`, `run_completed`, `run_failed`
- `builtin_tools.py` — 10 tools wrapping existing capabilities: `web_search`, `scrape_url`, `vault_get`, `knowledge_query`, `memory_recall`, `memory_store`, `code_exec`, `goal_set`, `risk_check`, `final_answer`
- Budget enforcement: `max_steps`, `max_tokens`, `max_seconds`

**Pillar 3 — Real Memory & Personalization** (`backend/memory/`)
- `store.py` — `MemoryStore` with 4 sub-stores: `user_profile`, `session_memory`, `semantic_memory`, `procedural_memory`
- `retrieval.py` — Hybrid ranking: 60% vector similarity + 30% recency+importance + 10% FTS, with profile-boost
- New SQLite tables: `user_profile`, `session_memory`, `semantic_memory`, `procedural_memory`
- Procedural memory tracks what approaches worked, picks the best on repeat tasks
- `format_context()` helper to render retrieval hits as LLM-readable context
- `embed_text()` helper for sentence-transformers embedding (optional, with numpy fallback)

**New API Surface** (16 new endpoints)
- `POST /v2/llm/chat` — Unified chat with optional streaming SSE
- `GET /v2/llm/providers` — List providers + models
- `POST /v2/agent/run` — Run the agent loop (streaming or non-streaming)
- `GET /v2/agent/tools` — List tools available to the agent
- `GET /v2/agent/history` — Recent agent runs
- `GET /v2/memory/profile` / `PUT /v2/memory/profile` — User profile CRUD
- `GET /v2/memory/sessions` / `GET/PUT /v2/memory/session/{id}` — Session memory
- `POST /v2/memory/store` — Store semantic memory
- `POST /v2/memory/recall` — Hybrid retrieval
- `DELETE /v2/memory/{id}` — Forget a memory
- `GET /v2/memory/procedural` / `POST /v2/memory/procedural/record` — Procedural patterns
- `GET /v2/memory/stats` — Memory statistics

**`/research` opt-in agent mode**
- `ResearchRequest` gained `use_agent: bool = False` — when `True`, the request delegates to the new ReAct loop, returning the legacy response shape + an `agent_run` block with full run metadata (steps, duration, tokens, cost, plan)
- Backward compat: all existing clients continue to work unchanged

**Frontend updates** (`frontend/jambubrowser-ui/`)
- New `AgentTimeline.tsx` component — visualizes agent steps as they happen (plan → tools → verification → answer)
- New `MemoryPanel.tsx` component — user profile, memory recall, session history
- New `utils/agent.ts` — SSE stream parser + `runResearchWithAgent()` helper
- New `utils/memory.ts` — memory API client
- New `utils/types.ts` — TypeScript types for new APIs
- `App.tsx` — `fullPower` now defaults to `True` (agent mode); `Cmd+M` keyboard shortcut opens memory panel
- `MessageList.tsx` — accepts `agentTimeline` prop, renders above messages, shows step count + cost + duration
- `Header.tsx` — "Memory" tab added with Brain icon; "GOD MODE" → "AGENT MODE" label

**Threading fix**
- `backend/core/database.py:52` — In-memory SQLite singleton now uses `check_same_thread=False` so FastAPI's threadpool can use it. Pre-existing issue surfaced by the new endpoints; this is the minimal-blast-radius fix.

### Tests — 78 new tests
- `tests/test_llm_layer.py` — 28 tests (base types, config, providers, registry, routing, tool format conversion)
- `tests/test_memory_system.py` — 25 tests (all 4 stores, retrieval, privacy scoping)
- `tests/test_agent_loop.py` — 25 tests (tool registry, builtin tools, plan parsing, verifier, events, agent loop)

**Total project test count: 75 unit + 30 E2E + 78 new = 183 tests**

### Environment variables (new)
- `JAMBU_LLM_PROVIDER` — `auto` (default), `anthropic`, `openai`, `ollama`, `mlx`, `minimax`, `mock`
- `JAMBU_LLM_MODEL` — override the default model for the selected provider
- `JAMBU_LLM_FALLBACK_CHAIN` — comma-separated provider list (default: `ollama,mlx,anthropic,openai,minimax`)
- `JAMBU_LLM_TIMEOUT` — per-request timeout in seconds (default: 30)
- `JAMBU_LLM_HEALTH_TIMEOUT` — health check timeout (default: 3)
- `JAMBU_LLM_MAX_TOKENS` — default max tokens (default: 1024)
- `JAMBU_LLM_TEMPERATURE` — default temperature (default: 0.3)
- `JAMBU_LLM_LOCAL_ONLY` — force `local_only` routing (privacy mode enforcement)
- `JAMBU_LLM_ANTHROPIC_MODEL`, `JAMBU_LLM_OPENAI_MODEL`, `JAMBU_LLM_OLLAMA_MODEL`, `JAMBU_LLM_MLX_MODEL`, `JAMBU_LLM_MINIMAX_MODEL` — per-provider model overrides
- `JAMBU_LLM_OPENAI_BASE_URL` — override the OpenAI-compatible base URL (e.g. for vLLM, Together)
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `MINIMAX_API_KEY` — provider API keys (existing)

## [2.1.0] - 2026-06-08

### Fixed
- PrivacyControls frontend interface now matches actual backend API response shape
- AuditLogViewer interface fields corrected (`by_category` not `categories`)
- Duplicate `/fingerprint/generate` and `/fingerprint/list` endpoint definitions removed
- `_expand_query` now uses dynamic LLM config instead of hardcoded `localhost:8080`
- WebSocket 404 error fixed by installing `websockets` package for uvicorn
- DuckDuckGo/Google CSP iframe block fixed with blank page placeholder
- Vite WebSocket proxy config corrected (`http://` target with `ws: true`)
- Backend CORS now includes port 5173 for frontend dev server
- `_call_llm` now checks Ollama availability before calling (3s health check)
- Fetch timeout (30s) prevents indefinite hangs when LLM unavailable
- AbortError properly caught and displayed as timeout message

### Added
- **AgentStatusBar**: Real-time WebSocket-powered agent state visualization
- **ErrorBoundary**: Crash protection wrapping the entire React app
- **VaultUnlock UI**: Password input and unlock flow for credential vault
- **Browser History**: Track visited URLs with timestamps and sidebar display
- **Keyboard Shortcuts**: Cmd+K (focus), Cmd+P (privacy), Cmd+L (audit), Cmd+1 (research), Cmd+T (new tab), Esc (close overlay)
- `/vault/unlock`, `/vault/lock`, `/vault/status` backend endpoints
- Privacy tab and Audit tab in Header (replaced non-functional "Stealth" tab)
- Vault tab in Header with KeyRound icon
- `vite-env.d.ts` for `import.meta.env` TypeScript support
- `useAgentWebSocket` hook for WebSocket agent state consumption
- `useKeyboardShortcuts` hook for global keyboard shortcuts
- `blank-page` CSS class for empty browser pane
- 30 E2E tests covering all major API endpoints
- Ollama health check in `_call_llm` (detects unavailable server in 3s)
- Comprehensive documentation: README, ARCHITECTURE, USER_GUIDE, DEVELOPER_GUIDE, API

### Changed
- Default browser URL changed from `google.com` to `about:blank`
- `_call_llm` timeout reduced from 30s to 10s
- Frontend `localFetch` now uses AbortController with 30s timeout
- Header tabs: Research, Intelligence, Workspace, Privacy, Audit, Vault
- `useAgentWebSocket` connects directly to backend (bypasses Vite WS proxy)

## [2.0.0] - 2026-06-07

### Added
- Complete React + Vite frontend with 13 components
- Split-view layout (30% chat + 70% browser)
- WebSocket agent state broadcasts
- Privacy controls with 4 modes
- Audit log viewer with live updates
- Knowledge graph with entity extraction
- Mission scheduler with cron expressions
- Consensus engine for multi-node voting
- Supply chain verification
- Browser fingerprint rotation
- Computer use (macOS screen capture, mouse/keyboard)
- Vision grounding (OCR, UI element detection)
- Multimodal input processing
- Skill synthesizer for auto tool creation
- P2P peer discovery
- Federated RAG
- YouTube transcript analysis
- Harness gateway compatibility layer

### Fixed
- Python 3.9 compatibility (Optional syntax, enable_load_extension fallback)
- Mission table schema migrations
- Database lock during knowledge ingestion
- Search module DuckDuckGo API fallback
- Playwright scraper fallback when crawl4ai unavailable

## [1.0.0] - 2026-06-06

### Added
- Initial release
- FastAPI backend with 60+ endpoints
- SQLite + sqlite-vec vector search
- Encrypted credential vault
- Tamper-evident audit logging
- Multi-engine search
- Browser session isolation
- PII detection and content sanitization
- Network isolation enforcement
