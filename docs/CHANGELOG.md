# Changelog

All notable changes to Jambubrowser.

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
