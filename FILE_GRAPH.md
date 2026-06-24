# Jambubrowser — File Graph & Purpose

> **What is this?** A new-intern-friendly map of every file in the project.
> Read this top-to-bottom. Each section explains *why* the file exists and *what* it does.

---

## Project Identity

**Jambubrowser** is a **Harness Product** — it straps together deep-internet browsing capability with AI orchestration to accelerate building software products.

### The Three Straps

| Strap | What It Provides | Key Modules |
|---|---|---|
| **🧭 Deep Internet Browsing** | Privacy-first, go-anywhere web access. SearXNG metasearch (90+ engines), Playwright automation, Tor/SOCKS routing, browser fingerprint rotation, PII redaction, SSRF protection. | `backend/modules/search.py`, `backend/modules/browser.py`, `backend/modules/playwright_scraper.py`, `backend/modules/fingerprint_rotator.py`, `backend/core/privacy.py`, `searxng/` |
| **🧠 AI Orchestration** | Unified LLM layer across 6 providers, ReAct agent loop (plan→execute→verify→replan), hybrid memory system, smart routing (cheapest/fastest/quality/fallback). | `backend/llm/`, `backend/agent/`, `backend/memory/`, `backend/llm/routing.py` |
| **🏗️ Product Building** | Multi-agent HarnessX orchestrator, sandboxed code execution, goal management, skill synthesis (learns from failures), evaluation framework, consensus engine for multi-node decisions, plugin system. | `backend/agent/harness.py`, `backend/core/sandbox.py`, `backend/modules/goal_orchestrator.py`, `backend/modules/skill_synthesizer.py`, `backend/eval/`, `backend/plugins/`, `backend/modules/consensus_engine.py` |

Everything plugs in via env vars, auto-registers via tool schemas, and wires through a configurable security middleware stack. It's a **platform for AI-augmented research and product development**, wrapped in a Tauri desktop shell with a React frontend and an iOS companion app.

---

## Root Level Files

| File | Purpose |
|---|---|
| `README.md` | Project overview, quick start, architecture diagram, full API table, and testing guide. Your first read. |
| `LICENSE` | MIT License. You can use/modify freely. |
| `SECURITY.md` | Security policy & vulnerability reporting process. |
| `docker-compose.yml` | Orchestrates 5 Docker services: engine, ollama (LLM), valkey/redis (cache), searxng (search), sandbox (code execution). For production/infra deployments. |
| `Dockerfile` | Multi-stage Docker build for the Python backend engine (slim image). |
| `requirements.txt` | Python dependencies: FastAPI, uvicorn, httpx, cryptography, sqlite-vec, sentence-transformers, MCP, etc. |
| `Makefile` | Canonical dev/test targets: `make test`, `make engine`, `make test-council`, etc. Maps to scripts in `tests/`. |
| `engine.py` | **Standalone entry point** (redundant with `backend/engine.py`). Legacy. |
| `engine.log` | Runtime log output from the engine. |
| `mcp.log` | MCP server log output. |
| `searxng.log` | SearXNG search engine log. |
| `rag_data.db` | SQLite database (generated at runtime) — vector embeddings, documents, missions, audit chain. |
| `.env` | Local environment variables (gitignored secrets — API keys, etc.). |
| `.env.example` | Template showing all available env vars (blocked from reading — contains secret templates). |
| `.gitignore` | Ignores: `__pycache__/`, `node_modules/`, `mlx-venv/`, `*.log`, `.env`, `rag_data.db`, etc. |
| `pyproject.toml` | (Missing — not yet created. Config would go here.) |

---

## `backend/` — Python FastAPI Backend

The core engine. FastAPI app serving 76+ endpoints across ~20 route modules.

### `backend/engine.py` — Application Factory (296 lines)

**The entry point.** Creates the FastAPI `app`, wires up all middleware (CORS, rate limiter, body size limit, GZip, request timeout, security headers, trusted host, request ID, access log), imports and registers all 20 route modules, sets up the lifespan (DB init, background tasks for memory audit & curiosity loop), and handles global exception handlers. Run via `uvicorn backend.engine:app`.

### `backend/engine_runtime.py` — Shared Runtime State (430 lines)

**The "utility belt"** every route module depends on:
- `ConnectionManager` — WebSocket connection pool with per-IP caps, broadcast helpers
- `safe_task()` — Wraps async tasks with error logging
- `_call_llm()` — Unified LLM call shim (delegates to `backend/llm/`)
- `_resolve_llm_config()` — Merges caller config → env-driven config → legacy defaults
- Agent state broadcasting (`broadcast_agent_state`, `broadcast_agent_telemetry`, etc.)
- Task tracking (`active_tasks`, `cancel_flags`, `broadcast_task_start/end`)

### `backend/core/` — Security & Infrastructure Modules

| File | Purpose |
|---|---|
| `database.py` | **SQLite DB singleton + schema init** (686 lines). Creates 16 tables: `documents`, `vec_documents` (vector search), `embedding_cache`, `missions`, `custom_tools`, `credential_vault`, `proposals`, `votes`, `browser_sessions`, `memory_entries` + FTS5 index, `sessions`, `task_metrics`, `tool_usage`, `provider_quota`, `session_analytics`. Provides `get_db()` / `get_db_cursor()` context managers. Has `smart_chunking()` for text splitting. Thread-safe with thread-local connections. Singleton for `:memory:` mode. |
| `privacy.py` | **Zero-trust privacy controls** (433 lines). Four-tier `PrivacyMode` enum (Standard/Enhanced/Maximum/Local-Only). `PIIDetector` — regex-based detection of emails, phones, SSNs, credit cards, IPs, MACs, passports, tracking codes (GA, FB Pixel, Mixpanel, Segment). `NetworkIsolator` — blocks tracking domains, sanitizes request headers. `ContentSanitizer` — masks PII, strips tracking params from URLs (utm_*, fbclid, gclid). `PrivacyManager` — orchestrates all controls. Module-level singleton via `get_privacy_manager()`. |
| `audit.py` | **Tamper-evident audit logger** (369 lines). SHA-256 hash chain: `Entry_N.hash = SHA256(entry_data + previous_hash)`, starts with `"genesis"`. Categories: RESEARCH, BROWSER, CREDENTIAL, NETWORK, PRIVACY, SYSTEM, ERROR. PII auto-redaction via shared `PIIDetector` + sensitive-key scrubbing (`password`, `token`, `api_key`, etc.). `verify_chain_integrity()` replays the chain to detect tampering. 90-day retention with auto-cleanup. Module-level singleton. |
| `vault.py` | **AES-256-GCM encrypted credential vault** (567 lines). `CredentialVault` singleton. Key derivation via PBKDF2-HMAC-SHA256 (480,000 iterations) with machine-specific salt (`~/.jambu/vault.salt`). Fernet encryption wrapper. Per-credential unique nonce. Auto-lock after 5 min inactivity. 5-failed-attempt → 5-min lockout. `SecureBuffer` for memory-zeroing on delete. `find_best_credential()` does URL-pattern scoring. Priority: `JAMBU_VAULT_KEY` env > `~/.jambu/vault.key` > auto-generate. |
| `vector_search.py` | **Two-tier vector search** (128 lines). Tier 1: sqlite-vec `MATCH` on `vec0` virtual table. Tier 2: numpy fallback — loads all embeddings into memory, computes cosine similarity. `store_embedding()` / `search_similar()` / `clear_embeddings()`. 384-dim float32 vectors. |
| `security.py` | **Input validation & SSRF protection** (124 lines). `is_safe_url()` — blocks private IP ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1/128, fc00::/7), only allows http/https schemes, max 8192 chars, detects DNS rebinding. `safe_filename()` — strips path separators, null bytes, whitelists `[\w.\- ]`. `is_safe_path()` — prevents traversal beyond allowed base dir. `sanitize_html()` — strips `<script>`, `on*=` handlers, `javascript:`. `validate_file_upload()` — blocks `.exe/.dll/.so/.dylib/.bat/.cmd/.sh/.bin` extensions. |
| `security_headers.py` | **Security headers middleware** (83 lines). Pure ASGI. Sets on every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera/mic/geo=()), `Content-Security-Policy` (self + localhost WS/HTTP), `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`. HSTS only on HTTPS. |
| `body_size_limit.py` | Middleware rejecting requests with body > 2 MB (413). |
| `trusted_host.py` | Middleware validating `Host` header against allowed hosts. Prevents DNS rebinding & Host injection. |
| `request_id.py` | **Correlation ID middleware** (70 lines). Assigns a `X-Request-ID` to every request (12-char hex UUID). Reuses client-provided ID if present (max 128 chars). Stores on `scope["request_id"]` for downstream use. Adds to response headers. |
| `request_timeout.py` | Middleware cancelling requests exceeding 30 seconds (with exclusion paths for long-running ops). |
| `access_log.py` | Middleware writing one structured JSON log line per request (method, path, status, duration, IP, request ID). |
| `security_events.py` | Centralised blocked-request audit — logs every rejected request with reason, IP, path. |
| `rate_limiter.py` | **Token-bucket rate limiter** (212 lines). Pure ASGI middleware. Per-IP + per-endpoint tracking. Configurable refill rate + burst per endpoint (e.g. `/research`: 2/s burst 5, `/mlx/generate`: 5/s burst 8, default POST: 30/s burst 60). Sends 429 + `X-Ratelimit-*` headers. Auto-cleanup of stale buckets (>1h idle). `RateLimitMiddleware` skips `/health` and `/ws`. |
| `supply_chain.py` | **Dependency integrity verification** (314 lines). `SupplyChainVerifier` singleton. Hashes all `.py` files in installed packages via SHA-256. Tracks known-good hashes in `~/.dependency_hashes.json`. Verifies critical packages (fastapi, uvicorn, cryptography, playwright, etc.), system components (Python, pip, Playwright, sqlite-vec), and binaries. |
| `sandbox.py` | **Sandboxed Python code execution** (354 lines). Two isolation backends: `SubprocessSandbox` (always available — subprocess with blocked imports, isolated mode `-I`, no `.pyc` `-B`, restricted env, 30s timeout, 100KB code limit) and `DockerSandbox` (stronger — `--network none`, `--read-only`, 256MB RAM, 1 CPU, tmpfs). Auto-selects Docker → subprocess fallback. `execute_sandboxed()` is the public API. |
| `socks.py` | **SOCKS5/Tor transport** (94 lines). Drop-in wrapper: `make_async_client()` replaces `httpx.AsyncClient()`. Routes through SOCKS5 proxy when `JAMBU_TOR_SOCKS_URL` or `AGENT_VPN_PROXY` is set. Normalizes `socks5h://` → `socks5://` (DNS over proxy is handled by protocol, not scheme). Lazy-imports `httpx-socks` — zero cost when Tor is disabled. |
| `llm_config.py` | Env-driven LLM provider config: reads `JAMBU_LLM_PROVIDER`, `JAMBU_LLM_*_MODEL`, `JAMBU_LLM_FALLBACK_CHAIN`, etc. |

### `backend/llm/` — Unified LLM Provider Layer (v3 pillar)

| File | Purpose |
|---|---|
| `base.py` | **Provider protocol + types** (288 lines). Defines `Provider` protocol: `chat()`, `stream()`, `health()`, `estimate_cost()`. Data types: `ChatMessage` (role/content/tool_calls), `Usage` (token counts + cost), `ChatResponse` (content/model/provider/usage/latency), `StreamChunk` (delta/finish_reason/usage). Error hierarchy: `ProviderError` → `ProviderTimeout`, `ProviderAuthError`, `ProviderRateLimit`, `ProviderUnavailable`. Pricing table for 12 model tiers ($/1M tokens). `estimate_cost_for_model()` computes cost. `normalize_llm_response()` strips `<think>` blocks and ```json fences. `collect_stream()` drains a stream into `ChatResponse`. |
| `config.py` | **Env-driven LLM config** (146 lines). `LLMConfig` dataclass loaded from `JAMBU_LLM_*` env vars. Configures: default provider (`auto`/specific), fallback chain (comma-separated, first healthy wins), per-provider credentials/URLs/models, timeouts (request 90s, health 3s), default params (max_tokens 1024, temp 0.3), `force_local_only` privacy mode. `reload_config()` for tests. |
| `registry.py` | **Provider registry + routing** (264 lines). `ProviderRegistry` singleton. Auto-discovers providers via `pkgutil.iter_modules` on `backend.llm.providers.*`. Lazy-instantiation on first `get()`. Default resolution: if `JAMBU_LLM_PROVIDER=auto`, iterates fallback chain checking health (respects `force_local_only`). Falls back to `mock` if nothing is healthy. `chat()`/`stream()` convenience methods backfill provider/model names and auto-compute cost. Latency tracking per provider. Thread-safe (RLock). |
| `routing.py` | Smart router: `cheapest`/`fastest`/`quality`/`fallback`/`local_only` strategies. Per-request cost tracking. |
| `providers/__init__.py` | Provider package init. |
| `providers/anthropic.py` | Anthropic Claude provider (Claude Sonnet 4). |
| `providers/openai.py` | OpenAI provider (GPT-4o). Supports custom base URLs (vLLM, Together, etc.). |
| `providers/ollama.py` | Ollama local LLM provider. |
| `providers/mlx.py` | Apple Silicon MLX provider (Gemma 4 12B via mlx-vlm). |
| `providers/minimax.py` | **MiniMax cloud provider** (203 lines). OpenAI-compatible `/v1/chat/completions`. Models: MiniMax-M2.7, M3, Text-01. Supports tools/function-calling. Streaming via SSE. Auth via `MINIMAX_API_KEY`. SOCKS-aware HTTP client. Error types: timeout → `ProviderTimeout`, connection refused → `ProviderUnavailable`, 401/403 → `ProviderAuthError`, 429 → `ProviderRateLimit`. |
| `providers/mock.py` | Mock provider for testing — returns canned responses. |

### `backend/agent/` — ReAct/Plan-Execute Agent Loop (v3 pillar)

| File | Purpose |
|---|---|
| `loop.py` | **ReAct/Plan-Execute agent loop** (466 lines). Algorithm: `for step in plan: execute tool → verify → if not advanced: replan`. SSE event stream for frontend live view. Budget-aware (max_steps, max_tokens, max_seconds). Supports native tool-use API (Anthropic/OpenAI). Accepts optional `HarnessConfig` for dependency-injected parameters. `AgentRunResult` accumulates usage/cost/duration across all steps. |
| `plan.py` | Plan generation — decomposes user query into atomic steps. |
| `tools.py` | Tool registry with auto-derived JSON Schema. Validates tool calls against schemas. |
| `builtin_tools.py` | **10 built-in agent tools** (420 lines). Each is a typed async function — auto-derives JSON Schema from type annotations. Tools: `web_search()` (SearXNG→DDG→Google), `scrape_url()` (Playwright with JS rendering), `vault_get()` (credential lookup), `knowledge_query()` (graph search), `memory_recall()` (semantic memory), `memory_store()` (save fact), `code_exec()` (sandboxed Python), `goal_set()` (sovereign goal), `risk_check()` (URL risk assessment), `final_answer()` (deliver answer). Manages Playwright browser lifecycle. |
| `verifier.py` | Step verification — checks if tool output matches expected result; triggers replan if not. |
| `events.py` | SSE event types and serialization. |
| `evolution.py` | Agent evolution — learns from past runs to improve planning. |
| `coevolution.py` | Co-evolution — multiple agent instances sharing learnings. |
| `digester.py` | Post-run digest — extracts patterns, success/failure signals from agent traces. |
| `harness.py` | HarnessX — multi-agent orchestrator for complex queries. |
| `harness_defaults.py` | Default configurations for the Harness orchestrator. |

### `backend/memory/` — Memory System (v3 pillar)

| File | Purpose |
|---|---|
| `store.py` | 4 sub-stores: user profile, session (conversation history), semantic (vector embeddings), procedural (action→outcome success rates). |
| `retrieval.py` | **Hybrid memory retrieval** (198 lines). Algorithm: 60% vector cosine similarity + 30% recency (`exp(-age/14days)`) + 10% FTS token-overlap. Profile-interest boost from user profile. Deduplication via cosine > 0.95. `RetrievalHit` tracks which sub-system matched. `embed_text()` using SentenceTransformer `all-MiniLM-L6-v2`. `format_context()` renders hits as LLM-readable context (max 2000 chars). |
| `migrations.py` | Schema migrations for memory tables. |

### `backend/routes/` — 20 Domain-Specific Route Modules

| File | Purpose |
|---|---|
| `system.py` | `/health`, `/stats` — health check (DB/audit/vault probes), database statistics. |
| `ws.py` | `/ws/{client_id}`, `/ws/audit` — WebSocket endpoints for agent state and live audit log. |
| `research.py` | `/research`, `/search` — autonomous research (query expansion → multi-engine search → LLM synthesis), raw metasearch. |
| `browser.py` | `/scrape`, `/act`, `/login`, `/exec` — page scraping, browser automation, vault login, sandboxed code exec. |
| `vault.py` | `/vault/status`, `/vault/unlock`, `/vault/lock`, `/vault/domains`, `/vault/credential` — credential vault CRUD. |
| `knowledge.py` | `/knowledge/ingest`, `/knowledge/graph`, `/knowledge/search`, `/knowledge/stats`, `/knowledge/entity`, `/knowledge/clusters` — entity-relation knowledge graph. |
| `memory.py` | `/memory/*` — CRUD for all 4 memory stores, plus hybrid recall. |
| `local.py` | Local-only endpoints (filesystem operations, local model queries). |
| `missions.py` | `/mission/*` — cron-based background research mission scheduler. |
| `tools.py` | Tool execution endpoints (calculator, etc.). |
| `models.py` | `/mlx/*` — MLX model management: status, server start/stop, list, download, generate. |
| `p2p.py` | `/p2p/*` — peer-to-peer node discovery and federated queries. |
| `goals.py` | `/goal/*` — sovereign goal management (set, track, approach, block, achieve). |
| `consensus.py` | `/consensus/*` — multi-node proposal/vote consensus engine. |
| `harness.py` | HarnessX bridge endpoints. |
| `v1.py` | Legacy v1 API endpoints (backward compatibility). |
| `v2.py` | **New v2 API**: `/v2/llm/chat`, `/v2/agent/run`, `/v2/memory/*` — unified chat, agent loop, memory CRUD. |
| `multimodal.py` | `/multimodal/image`, `/multimodal/text`, `/multimodal/file` — multimodal input processing. |
| `fingerprint.py` | `/fingerprint/generate`, `/fingerprint/rotate`, `/fingerprint/list`, `/fingerprint/profile` — browser fingerprint management. |
| `media.py` | Media file handling endpoints. |

### `backend/modules/` — Business Logic Modules

| File | Purpose |
|---|---|
| `search.py` | **Multi-engine metasearch** (307 lines). Primary: SearXNG at `localhost:8888/search`. Fallback: DuckDuckGo API via `duckduckgo_search` library → DDG instant answer API. Google scrape as last resort. SOCKS-aware (Tor routing). `multi_engine_search()` runs all engines in parallel, deduplicates by URL, returns ranked results. `expand_query()` uses LLM to generate 3 diverse search queries from user input. |
| `browser.py` | Browser session isolation, privacy wrapper, Playwright lifecycle management. |
| `scraper.py` | Web scraper: crawl4ai → Playwright → httpx fallback. HTML→Markdown conversion. |
| `playwright_scraper.py` | Dedicated Playwright-based scraper for JavaScript-heavy pages. |
| `vision.py` | Vision model integration: OCR, UI element detection, screen verification. |
| `fingerprint_rotator.py` | Browser fingerprint generation & rotation (user agent, canvas, WebGL, audio). |
| `knowledge_graph.py` | Entity extraction, relationship inference, topic clustering, graph storage/retrieval. |
| `missions.py` | Cron-based background research mission scheduler. |
| `consensus_engine.py` | Multi-node voting protocol: propose, vote, tally, resolve. |
| `mlx_provider.py` | **Apple Silicon MLX integration** (528 lines). Model registry for Gemma 4 variants (4-bit, MXFP4, 6-bit, 8-bit) with RAM/disk requirements. Server lifecycle: start/stop OpenAI-compatible VLM server on port 8080. Direct inference via `mlx_lm.generate()`. Model download from HuggingFace. Cache management. `is_mlx_available()` checks for MLX Python package. SOCKS-aware HTTP. |
| `form_filler.py` | Auto-fill web forms with vault credentials (detect → match → fill). |
| `p2p_discovery.py` | UDP broadcast peer discovery for multi-node research mesh. |
| `federated_rag.py` | Federated RAG — query trusted peers for answers. |
| `risk_shield.py` | URL risk assessment — checks URLs against threat databases before visiting. |
| `shadow_browser.py` | Autonomous shadow browser — runs background research without user interaction. |
| `goal_orchestrator.py` | **Sovereign goal orchestration** (593 lines). `GoalOrchestrator` manages the full goal lifecycle: create, activate, approach, block, fallback, achieve. Injects goal context into every user query. Tracks approaches tried (hypothesis → result → evidence → learning). Generates fallback strategies via LLM when stuck. Goal persistence in `~/.jambu/goals/`. RAG integration feeds documentation back into the learning loop. |
| `harness_bridge.py` | Bridge to the HarnessX multi-agent orchestrator. |
| `multimodal_input.py` | Process images, files, and text through multimodal models. |
| `model_manager.py` | LLM model lifecycle management (download, cache, verify). |
| `notifications.py` | Native OS notification system (macOS). |
| `youtube.py` | YouTube transcript extraction and video analysis. |
| `skill_synthesizer.py` | **Autonomous skill synthesis** (410 lines). Flow: 1) detect failure → classify error type (scraping/parsing/auth/selector/rate_limit/cert/timeout), 2) LLM generates Python script, 3) sandbox-tests it, 4) iterates on failures (max 3 attempts), 5) persists successful tool to DB. Maintains a toolbox of reusable skills across sessions. |
| `local_connector.py` | Local machine connector (file system, clipboard, apps). |
| `ai_gateway.py` | AI gateway — routes requests to appropriate AI backends. |

### `backend/scripts/`

| File | Purpose |
|---|---|
| `mlx_vlm_server.py` | Standalone FastAPI server for MLX VLM inference (OpenAI-compatible). Runs Gemma 4 12B on Apple Silicon. |

### `backend/plugins/`

| File | Purpose |
|---|---|
| `manager.py` | Plugin system — loads, configures, and manages plugins. |
| `__init__.py` | Plugin package init. |

### `backend/tools/`

Empty directory. Tools are defined in `backend/agent/builtin_tools.py`.

### `backend/eval/` — Evaluation Framework

| File/Module | Purpose |
|---|---|
| `__main__.py` | CLI entry point for the evaluation harness. |
| `cli.py` | Command-line interface for running evals. |
| `harness.py` | Evaluation harness — runs tasks, collects metrics. |
| `metrics.py` | Evaluation metrics calculation (accuracy, latency, cost). |
| `report.py` | Report generation from eval results. |
| `store.py` | Evaluation result storage. |
| `runners/` | Task runner implementations. |
| `tasks/` | Pre-defined evaluation tasks: `smoke.py` (sanity checks), `gaia.py`/`gaia_mini.py` (GAIA benchmark), `webarena_mini.py` (web agent), `webshop.py` (shopping), `swebench.py` (software engineering), `memory.py` (memory tests), `privacy.py` (privacy tests), `alfworld.py` (text games). |

---

## `browser-app/` — Frontend (React + Vite + Tauri)

The canonical frontend. Single React 19 app for both web (`npm run dev`) and desktop Tauri (`npm run tauri dev`).

### Config & Build Files

| File | Purpose |
|---|---|
| `package.json` | npm dependencies: React 19, Tailwind v4, Framer Motion, cmdk, react-force-graph-2d, vite, vitest. |
| `vite.config.ts` | Vite config — dev server on port 1420, proxy to backend on :8001. |
| `vitest.config.ts` | Vitest test runner config. |
| `tsconfig.json` | TypeScript config for the React app. |
| `tsconfig.node.json` | TypeScript config for Node/Vite tooling. |
| `eslint.config.js` | ESLint flat config. |
| `index.html` | HTML entry point for the Vite dev server. |

### `src/` — React Source

| File | Purpose |
|---|---|
| `main.tsx` | React entry point — mounts `<App />`. |
| `App.tsx` | **Main app component** — layout orchestration, SSE agent handling, tab management, all state lives here. |
| `vite-env.d.ts` | Vite type declarations. |

### `src/components/` — UI Components

| Path | File | Purpose |
|---|---|---|
| `layout/` | `AppShell.tsx` | 4-pane shell: TopBar, Sidebar, Main Canvas, Inspector, StatusBar. |
| `layout/` | `TopBar.tsx` | Top header bar — workspace selector, model selector, privacy toggle, command palette button. |
| `layout/` | `Sidebar.tsx` | Collapsible sidebar (⌘B) — navigation between Research, Browser, Privacy, Audit, Memory. |
| `layout/` | `StatusBar.tsx` | Bottom footer — WebSocket connection status, live telemetry, privacy mode, cost tracker. |
| `chat/` | `ChatPane.tsx` | Research chat interface — streaming messages, input box, send button. |
| `chat/` | `MessageCard.tsx` | Individual user/assistant message bubble with source chips and metadata. |
| `chat/` | `AgentTimeline.tsx` | Live agent reasoning timeline — Plan → Execute → Verify steps with streaming deltas. |
| `chat/` | `AgentWorking.tsx` | "Agent is working" animated indicator. |
| `browser/` | `BrowserPane.tsx` | Inline iframe sandbox with URL bar, tab management, navigation controls. |
| `knowledge/` | `KnowledgeMini.tsx` | Lightweight 2D force-directed knowledge graph visualization. |
| `inspector/` | `InspectorPanel.tsx` | Context-aware right panel showing details of selected items. |
| `privacy/` | `PrivacyControls.tsx` | 4-mode privacy selector (Standard/Enhanced/Maximum/Local-Only) with live report. |
| `audit/` | `AuditLogViewer.tsx` | Live streaming audit log viewer with category filtering. |
| `vault/` | `VaultUnlock.tsx` | Credential vault unlock form. |
| `memory/` | `MemoryPanel.tsx` | Full memory management: profile editing, recall search, session list, store form. |
| `command/` | `CommandPalette.tsx` | ⌘K command palette (cmdk) — quick actions, search. |
| `onboarding/` | `OnboardingWizard.tsx` | First-run setup wizard + help (reopen via ⌘?). |
| `ui/` | `button.tsx` | Reusable shadcn-style button component. |
| `ui/` | `dialog.tsx` | Reusable dialog/modal component. |

### `src/utils/` — Utilities & API

| File | Purpose |
|---|---|
| `api.ts` | HTTP client — `localFetch()` with 30s timeout, all API call functions. |
| `api.test.ts` | Tests for the API client. |
| `types.ts` | TypeScript types/interfaces for the entire app (messages, tabs, metrics, etc.). |
| `agent.ts` | Agent SSE stream parser — converts SSE events into React state updates. |
| `memory.ts` | Memory API client functions. |
| `useAgentWebSocket.ts` | React hook for WebSocket connection to agent state broadcasts. |
| `useKeyboardShortcuts.ts` | React hook for keyboard shortcut bindings (⌘K, ⌘B, ⌘L, etc.). |

### `src/store/`

| File | Purpose |
|---|---|
| `appStore.ts` | Zustand-like app state store (tabs, messages, settings). |
| `appStore.test.ts` | Tests for the app store. |

### `src/styles/`

| File | Purpose |
|---|---|
| `globals.css` | Global CSS — Tailwind directives, CSS variables, base styles. |

### `src/lib/`

| File | Purpose |
|---|---|
| `utils.ts` | General utility functions (class name merging, formatting, etc.). |

### `src/test/`

| File | Purpose |
|---|---|
| `setup.ts` | Vitest setup — test environment configuration. |
| `llm-integration.test.ts` | Frontend LLM integration tests. |

### `src-tauri/` — Tauri 2 Desktop Shell (Rust)

| File | Purpose |
|---|---|
| `tauri.conf.json` | Tauri configuration — window size, CSP, bundle settings, updater endpoints, deep link scheme. |
| `Cargo.toml` | Rust dependencies. |
| `build.rs` | Build script for the Tauri app. |
| `Info.plist` | macOS bundle metadata (CFBundleName, CFBundleURLTypes, etc.). |
| `entitlements.plist` | macOS code signing entitlements. |
| `src/main.rs` | Tauri app entry point. |
| `src/lib.rs` | Plugin registration (opener, shell, updater, notification, process, deep-link). |
| `src/commands/mod.rs` | Module declarations for Rust commands. |
| `src/commands/proxy.rs` | `proxy_localhost` Tauri command — forwards HTTP from WebView to backend. |
| `src/commands/system.rs` | System commands (platform info, app info). |
| `src/orchestrator/mod.rs` | Rust orchestrator module declarations. |
| `src/orchestrator/services.rs` | Service management — spawns Python backend & sidecar processes. |
| `binaries/` | Contains `llama-server` sidecar binary for local LLM inference. |
| `capabilities/default.json` | Window permissions for the Tauri app. |
| `icons/` | App icons for all platforms. |

---

## `ios-app/` — Native iOS App (Swift/SwiftUI)

| Path | Purpose |
|---|---|
| `Jambubrowser/JambubrowserApp.swift` | SwiftUI app entry point. |
| `Jambubrowser/ContentView.swift` | Main SwiftUI view. |
| `Jambubrowser/Views/` | SwiftUI views for the iOS app. |
| `Jambubrowser/Services/` | iOS-specific services (networking, auth). |
| `Jambubrowser/Background/` | Background task handlers. |
| `JambubrowserKit/JambubrowserKit.swift` | Shared framework for iOS app logic. |
| `JambubrowserKit/Services/` | Shared services between app and extensions. |

---

## `frontend/` — Legacy (Deprecated)

| File | Purpose |
|---|---|
| `jambubrowser-ui/` | Old React 18 frontend. **Removed/archived** — use `browser-app/` instead. |

---

## `tests/` — Python Test Suite

575+ passing tests. Organised by module:

| File | What It Tests |
|---|---|
| `conftest.py` | Shared pytest fixtures and configuration. |
| `test_backend.py` | Core backend functionality (~22 tests). |
| `test_engine.py` | Integration tests using FastAPI TestClient. |
| `test_engine_runtime.py` | Engine runtime (ConnectionManager, safe_task, broadcast helpers). |
| `test_llm_layer.py` | LLM provider layer — all 6 providers, registry, routing (~28 tests). |
| `test_memory_system.py` | Memory system — 4 stores, hybrid retrieval (~25 tests). |
| `test_agent_loop.py` | ReAct agent loop — plan/execute/verify cycle (~25 tests). |
| `test_core_security.py` | SSRF protection (`is_safe_url`), path validation. |
| `test_security_headers.py` | CSP, HSTS, X-Frame-Options middleware. |
| `test_body_size_limit.py` | Body size limit middleware. |
| `test_trusted_host.py` | Trusted host middleware. |
| `test_request_id.py` | Request ID correlation middleware. |
| `test_request_timeout.py` | Request timeout middleware. |
| `test_error_sanitization.py` | Error sanitization (hides `str(exc)` in production). |
| `test_security_events.py` | Blocked-request audit logging. |
| `test_access_log.py` | Structured access log middleware. |
| `test_calculator.py` | Calculator tool. |
| `test_audit_redaction.py` | Audit log PII redaction. |
| `test_privacy.py` | Privacy modes, network isolation, PII detection. |
| `test_supply_chain.py` | Dependency integrity verification. |
| `test_health_endpoint.py` | Health check endpoint. |
| `test_exec_request.py` | Code execution endpoint validation. |
| `test_e2e.py` | End-to-end tests (~30 tests, requires running backend). |
| `test_mcp_server.py` | MCP server stdio smoke test (21 tools). |
| `test_socks.py` | SOCKS5/Tor proxy integration. |
| `test_eval.py` | Evaluation framework. |
| `test_eval_cli.py` | Eval CLI. |
| `test_real_llm_integration.py` | Real LLM integration tests. |
| `test_search_integration.py` | Search engine integration tests. |
| `test_research_integration.py` | Research pipeline integration tests. |
| `test_deferred.py` | Deferred execution tests. |
| `test_phase2.py` | Phase 2 legacy tests. |
| `test_phase3.py` | Phase 3 legacy tests. |
| `test_phase4.py` | Phase 4 legacy tests. |
| `test_phase5.py` | Phase 5 legacy tests. |
| `bench_browser.py` | Browser efficiency benchmarks. |
| `bench_harness_efficiency.py` | HarnessX efficiency benchmarks. |
| `bench_claude_cli_mcp.py` | Claude CLI MCP benchmarks. |
| `council.py` | **The Council** — runs every gate in one command (integration test orchestrator). |
| `full_e2e_real_llm.py` | Full E2E with real LLM (not mock). |
| `smoke_harnessx_e2e.py` | HarnessX smoke test (7 stages). |
| `_engine.py` | Engine test helper. |

---

## `tools/` — Python Utility Tools

| File | Purpose |
|---|---|
| `calculator.py` | A simple calculator tool (used by the agent loop). |
| `test_calculator.py` | Tests for the calculator tool. |
| `mcp/` | MCP server tool implementations. |
| `test_exec_tool.py` | Tests for execution tool. |
| `test_tool_123.py` | Generic tool test. |
| `test_tor_sessions.py` | Tor session isolation tests. |

---

## `docs/` — Documentation

| File | Purpose |
|---|---|
| `README.md` | (In root — see above.) |
| `API.md` | Complete API reference for all endpoints. |
| `ARCHITECTURE.md` | Deep-dive technical architecture (middleware, request flow, privacy layers, database schema). |
| `CHANGELOG.md` | Version history and release notes. |
| `DEVELOPER_GUIDE.md` | Development setup, coding conventions, PR workflow. |
| `USER_GUIDE.md` | End-user guide — how to use Jambubrowser. |
| `FEATURES.md` | Comprehensive features list. |
| `EVAL.md` | Evaluation framework documentation. |
| `UI_REDESIGN_v4.md` | Plans for the v4 UI redesign. |

---

## `scripts/` — Development Shell Scripts

| File | Purpose |
|---|---|
| `dev.sh` | **Single command** to boot the full stack: backend + LLM + Tauri. |
| `build.sh` | Production build for current platform (auto-detects). `--skip-signing` for unsigned builds, `--target` for cross-compile. |
| `sign.sh` | Sign + notarize an existing macOS `.app` bundle. |
| `gen-updater-keys.sh` | Generate Tauri auto-updater keypair (public + private). |

---

## `searxng/` — SearXNG Meta-Search Engine (Fork)

A full fork of the [SearXNG](https://github.com/searxng/searxng) open-source metasearch engine. Provides privacy-preserving web search across 90+ engines. Configured via:

| File | Purpose |
|---|---|
| `settings.yml` (in `searxng-config/`) | SearXNG configuration — enabled engines, rate limits, formats. |
| `searxng/` | Full SearXNG source tree. |

---

## `bin/`

| File | Purpose |
|---|---|
| `micromamba` | Micromamba binary — fast conda package manager alternative (used for environment management). |

---

## `models/`

| File | Purpose |
|---|---|
| `.cache/` | Model cache directory (gitignored). Stores downloaded LLM/VLM model files. |

---

## `mlx-venv/` (gitignored)

Python virtual environment for MLX (Apple Silicon) — contains `mlx-lm` and `mlx-vlm`.

---

## `searxng-env/` (gitignored)

Conda environment for SearXNG (legacy, unused if Docker is used).

---

## `staged_repo/` (gitignored)

Staged repository backup (used during development/release).

---

## `llama-b9496/` (gitignored)

Legacy llama.cpp binary directory (superseded by MLX).

---

## `__pycache__/` (gitignored)

Python bytecode cache (auto-generated).

---

## `.github/` — CI/CD

| File | Purpose |
|---|---|
| `workflows/test.yml` | CI — runs on every push/PR. 4 jobs: backend tests (Python 3.9-3.12 matrix), frontend build+lint, backend integration, lint+format. |
| `workflows/release.yml` | CD — runs on `v*` tags. Matrix-builds for macOS (aarch64+x86_64), Linux (deb/AppImage), Windows (msi/exe). Signs, notarizes, creates GitHub Release. |
| `dependabot.yml` | Weekly auto-updates for Python, npm, Cargo, GitHub Actions deps. |

---

## `.omo/` — OpenCode Workspace Config

| File | Purpose |
|---|---|
| `plans/` | Saved work plans (OpenCode AI agent planning). |
| `run-continuation/` | Run continuation state. |

---

## Quick Orientation for a New Intern

```
You need to...                    Start here...
─────────────────────────────────────────────────────────────
Understand the whole project      README.md + docs/ARCHITECTURE.md
Understand the harness vision     This file — FILE_GRAPH.md
Run the backend                   python3 -m uvicorn backend.engine:app
Run the frontend                  cd browser-app && npm run dev
Run everything at once            ./scripts/dev.sh
Add a new API endpoint            backend/routes/ + engine.py
Add a new frontend page           browser-app/src/components/ + App.tsx
Wire a new LLM                    backend/llm/providers/ + registry.py
Add a new agent tool              backend/agent/builtin_tools.py + tools.py
Add a new memory store            backend/memory/store.py + retrieval.py
Deep-internet search tweak        backend/modules/search.py + searxng-config/
Privacy/security layer            backend/core/*.py (each middleware is one file)
Run an evaluation                 backend/eval/ + make test-council
Debug a test failure              tests/test_*.py (pick the right module)
Build for production              ./scripts/build.sh
```
