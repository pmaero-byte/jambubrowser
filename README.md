# Jambubrowser

**AI employees that audit, research, and improve your web apps — autonomously.**

Jambubrowser is a privacy-first autonomous AI platform. A team of specialist
**AI employees** (security, performance, UX, accessibility, SEO, code-quality)
audits your web apps by driving a real headless browser, then routes findings to
human teams for resolution. Underneath sits a sovereign research engine
(multi-engine search, local LLMs, knowledge graph, sandboxed code execution)
that powers both the audits and free-form research.

---

## Quick Start

### Prerequisites
- Python 3.9+
- Node.js 18+
- **Ollama** (for local LLM via Ollama) **or** **MLX** (Apple Silicon-native inference, recommended)

### Backend
```bash
# Install backend dependencies
pip install -r requirements.txt

# Start the engine
python3 -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001
```

### MLX LM Setup (Apple Silicon, Recommended)
```bash
# Create MLX virtual environment (requires Python 3.11+)
python3.11 -m venv mlx-venv
mlx-venv/bin/pip install mlx-lm mlx-vlm

# Start the MLX VLM server with Gemma 3 12B (auto-downloads from HuggingFace)
mlx-venv/bin/python3 backend/scripts/mlx_vlm_server.py \
  --model mlx-community/gemma-3-12b-it-4bit \
  --port 8080
```

Then send research requests with `"llm_provider": "mlx"`.

### Frontend (single canonical UI)
```bash
cd browser-app
npm install
npm run dev     # Vite dev server (port 1420)
# or for the desktop shell:
npm run tauri dev
```

The old `frontend/jambubrowser-ui/` React 18 app has been removed. The Tauri app
(`browser-app/`) now serves as both the desktop and web target.

### LLM Provider Configuration (v3)
Configure providers via env vars (all optional, sensible defaults):

```bash
# Default: "auto" picks the first healthy provider in the fallback chain
export JAMBU_LLM_PROVIDER="auto"

# Override the fallback chain (order matters — first healthy wins)
export JAMBU_LLM_FALLBACK_CHAIN="ollama,mlx,anthropic,openai,minimax"

# Privacy: refuse cloud providers (max privacy mode)
export JAMBU_LLM_LOCAL_ONLY="true"

# Per-provider credentials
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export MINIMAX_API_KEY="..."

# Per-provider model + base URL overrides
export JAMBU_LLM_ANTHROPIC_MODEL="claude-sonnet-4-5"
export JAMBU_LLM_OPENAI_MODEL="gpt-4o"
export JAMBU_LLM_OLLAMA_MODEL="gemma3:4b"
export JAMBU_LLM_OPENAI_BASE_URL="https://api.openai.com/v1"  # or vLLM, Together, etc.

# Then in the CommandBar, pick a provider from the dropdown, or use "auto".
```

V2 endpoints exposed by the running engine:
- `POST /v2/llm/chat` — unified chat (any provider, optional streaming)
- `POST /v2/agent/run` — ReAct/Plan-Execute loop with SSE events
- `GET/POST /v2/memory/*` — 4-store memory CRUD + hybrid recall

### Test
```bash
# Unit tests (~22 tests)
python3 -m pytest tests/test_backend.py -v

# E2E tests (~30 tests, requires running backend)
python3 -m pytest tests/test_e2e.py -v
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React + Vite Frontend                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Research  │ │ Browser  │ │ Privacy  │ │  Audit Log   │   │
│  │  Chat     │ │  Pane    │ │ Controls │ │   Viewer     │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Tab Sys  │ │ Command  │ │ Metrics  │ │ Agent Status │   │
│  │          │ │   Bar    │ │  Panel   │ │    Bar       │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTP + WebSocket (with X-Request-ID)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              Security Middleware Stack (v3.3+)               │
│  AccessLog → RequestID → TrustedHost → SecurityHeaders      │
│  → RequestTimeout → GZip → BodySizeLimit → RateLimit        │
│  (every request gets a correlation ID, CSP/HSTS headers,    │
│   request timeouts, body limits, per-IP rate limits)        │
└─────────────────────┬───────────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  Python FastAPI Backend                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Research │ │ Browser  │ │ Privacy  │ │  Knowledge   │   │
│  │  Engine  │ │ Manager  │ │ Manager  │ │    Graph     │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Search   │ │ Fingerprint│ │ Mission │ │  Consensus   │   │
│  │ Manager  │ │ Rotator  │ │Scheduler│ │    Engine    │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Audit    │ │ Vault    │ │ Sandbox  │ │  Supply Chain│   │
│  │ Logger   │ │ (AES256) │ │ Executor │ │  Verifier    │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌────────────────────────────────────────────────────┐    │
│  │ URL Validation Layer: is_safe_url() on every       │    │
│  │ URL-accepting endpoint (SSRF protection)           │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                     SQLite + sqlite-vec                      │
│  documents | vec_documents | missions | credential_vault    │
│  proposals | votes | browser_sessions | memory_entries      │
│  task_metrics | tool_usage | provider_quota | sessions      │
│  audit_log (tamper-evident, SHA-256 chain)                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Features

### AI Employee Audit Engine (the headline feature)
- **6 Specialist LLM Auditors**: Security, Performance, UX/UI, Accessibility, SEO, Code-Quality — each an autonomous "employee" that analyzes a live page.
- **Real Browser Telemetry**: Playwright-driven collection of network requests, console errors, DOM, accessibility tree, and performance metrics.
- **Parallel Dispatch + SSE Streaming**: All employees run concurrently; findings stream live to the `AuditPanel`.
- **Product-Context Extraction**: Auto-infers what the app *does* and assigns business-impact scores to findings.
- **Teams & Resolution Workflow**: Findings route to teams, get assigned, tracked, and marked resolved (`/teams/*`).
- **Persistence & Sharing**: Full audit history + shareable read-only links (`/audit/history`, `/audit/shared/{token}`).

### Agentic Research (v3 — the engine underneath)
- **Unified LLM Layer**: 6 providers (Anthropic, OpenAI, Ollama, MLX, MiniMax, Mock) behind one `Provider` protocol. Auto-discovery, env-driven defaults, smart routing (`cheapest` / `fastest` / `quality` / `fallback` / `local_only`), per-request cost tracking.
- **ReAct Agent Loop**: Plan → Execute → Verify → Replan, with streaming SSE events. Auto-derived JSON Schema for every tool, 10 built-in tools wrapping existing capabilities (web_search, scrape_url, vault_get, knowledge_query, memory_recall, memory_store, code_exec, goal_set, risk_check, final_answer). Budget-aware (max steps / tokens / seconds).
- **Memory & Personalization**: 4 sub-stores (user profile, session, semantic with embeddings, procedural with success rates). Hybrid retrieval: 60% vector + 30% recency+importance + 10% FTS, with profile-interest boost. Per-user scoping, full forget support.

### Privacy & Security
- **4 Privacy Modes**: Standard, Enhanced, Maximum, Local-Only
- **PII Detection**: Auto-redacts emails, phones, SSNs, credit cards, IPs, MACs, passports
- **Tracking Protection**: Blocks Google Analytics, Facebook Pixel, Mixpanel, etc.
- **Credential Vault**: AES-256-GCM encrypted with PBKDF2 (480k iterations)
- **Tamper-Evident Audit Log**: SHA-256 hash chain of all actions
- **Supply Chain Verification**: Hash verification for all Python dependencies
- **Tor Routing**: SOCKS5 proxy support for anonymous research
- **Browser Fingerprint Rotation**: Unique profiles per session
- **SSRF Protection**: `is_safe_url()` blocks private IPs, DNS rebinding, and
  unsafe schemes on every URL-accepting endpoint
- **Security Middleware Stack** (v3.3.0+):
  - Trusted host validation (rejects untrusted `Host` headers)
  - Body size limit (2 MB cap with 413 on oversized requests)
  - Request timeout (30 s default, exclusions for long-running paths)
  - Rate limiting (per-IP + per-endpoint token bucket)
  - Security headers (CSP, HSTS on HTTPS, X-Frame-Options, Permissions-Policy)
  - Request ID propagation (correlation ID on every log/error)
  - GZip compression
  - Access logging (one structured line per request)
- **WebSocket Hardening**: client_id validation, per-IP and global
  connection caps, clean socket replacement on reconnect
- **Error Sanitization**: `str(exc)` hidden in production (`JAMBU_DEBUG=false`)
- **Input Validation**: Pydantic field bounds on `/exec` (timeout, code size),
  `/research` (query, top_n, domain)

### Research & Intelligence
- **Multi-Engine Search**: SearXNG → DuckDuckGo API → Google fallback
- **Hybrid RAG Pipeline**: SQLite-vec vector search + semantic memory
- **Knowledge Graph**: Entity extraction, relationship inference, topic clustering
- **Swarm Research**: Decomposes complex queries into parallel sub-missions
- **LLM Synthesis**: Local (Ollama/MLX/Gemma4) + Cloud (MiniMax) support
- **MLX LM Provider**: Apple Silicon-native inference via `mlx-vlm` — 12-33 tok/s on M4 Pro with Gemma 3 12B, 4 privacy modes, local-first
- **Brain-Only Mode**: Search local knowledge vault without web access

### Browser & Automation
- **Multi-Tab Management**: Add, close, switch tabs
- **Playwright Integration**: Headless browser automation
- **Computer Use**: macOS screen capture, mouse/keyboard control
- **Form Filler**: Auto-fill login forms with vault credentials
- **Vision Grounding**: OCR, UI element detection, screen verification

### Frontend
- **4-Pane Agent Shell**: TopBar, Sidebar (collapsible ⌘B), Main Canvas, Inspector, StatusBar
- **Real-Time Agent Status**: WebSocket-powered live state + telemetry in status bar
- **Keyboard Shortcuts**: ⌘K command palette, ⌘B sidebar, ⌘L logs, ⌘⇧M memory, ⌘T new tab, ⌘⇧P privacy, ⌘? help
- **Unified Codebase**: Single React 19 app for desktop (Tauri) and web builds
- **Browser History**: Track visited URLs with timestamps

### Infrastructure
- **WebSocket API**: Live audit logs and agent state updates
- **Rate Limiting**: Configurable per-endpoint rate limits
- **Global Exception Handling**: Consistent error responses
- **CORS Support**: Configured for local development
- **Mission Scheduler**: Cron-based background research tasks
- **P2P Discovery**: UDP broadcast for multi-node research mesh

---

## API Endpoints

| Category | Endpoint | Method | Description |
|----------|----------|--------|-------------|
| System | `/health` | GET | Health check (with DB/audit/vault probes) |
| System | `/stats` | GET | Database statistics |
| Research | `/research` | POST | Autonomous research (validated: query, top_n, domain) |
| Research | `/search` | GET | Raw metasearch |
| Browser | `/scrape` | POST | Single page scraping (URL validated) |
| Browser | `/act` | POST | Browser automation |
| Browser | `/login` | POST | Credential vault login |
| Browser | `/exec` | POST | Sandboxed code execution (validated: timeout, code size) |
| Privacy | `/privacy/report` | GET | Privacy report |
| Privacy | `/privacy/mode` | POST | Set privacy mode |
| Privacy | `/privacy/check` | GET | Check URL allowance |
| Audit | `/audit/stats` | GET | Audit statistics |
| Audit | `/audit/log` | GET | Audit entries |
| Audit | `/audit/verify` | GET | Chain verification |
| Vault | `/vault/status` | GET | Vault lock status |
| Vault | `/vault/unlock` | POST | Unlock vault |
| Vault | `/vault/lock` | POST | Lock vault |
| Vault | `/vault/domains` | GET | List credential domains |
| Security | `/security/verify` | GET | Supply chain report |
| Fingerprint | `/fingerprint/generate` | POST | Generate fingerprint |
| Fingerprint | `/fingerprint/rotate` | POST | Rotate fingerprint |
| Knowledge | `/knowledge/ingest` | POST | Ingest to knowledge graph |
| Knowledge | `/knowledge/graph` | GET | Graph visualization data |
| Missions | `/mission/schedule` | POST | Schedule mission |
| Missions | `/mission/list` | GET | List missions |
| Consensus | `/consensus/propose` | POST | Create proposal |
| Consensus | `/consensus/vote` | POST | Cast vote |
| Vision | `/vision/ocr` | POST | Extract text from image |
| Vision | `/vision/ui-elements` | POST | Detect UI elements |
| Computer | `/computer/capture` | GET | Screen capture |
| Computer | `/computer/mouse` | POST | Mouse control |
| Computer | `/computer/keyboard` | POST | Keyboard input |
| Multimodal | `/multimodal/image` | POST | Process image |
| Multimodal | `/multimodal/text` | POST | Process text |
| MLX | `/mlx/status` | GET | MLX provider status |
| MLX | `/mlx/server/start` | POST | Start MLX VLM server |
| MLX | `/mlx/server/stop` | POST | Stop MLX server |
| MLX | `/mlx/models` | GET | List available MLX models |
| MLX | `/mlx/models/download` | POST | Download MLX model |
| MLX | `/mlx/generate` | POST | Direct MLX inference |
| WebSocket | `/ws/{client_id}` | WS | Agent state updates |
| WebSocket | `/ws/audit` | WS | Live audit log |

See [docs/API.md](docs/API.md) for complete API reference.

---

## Frontend Components

All components live in `browser-app/src/` and are shared between the desktop (Tauri) and web builds.

| Component | File | Description |
|-----------|------|-------------|
| App | `src/App.tsx` | Main layout orchestration + agent SSE handling |
| AppShell | `src/components/layout/AppShell.tsx` | 4-pane shell: TopBar, Sidebar, Canvas, Inspector, StatusBar |
| TopBar | `src/components/layout/TopBar.tsx` | Workspace/model/privacy/command palette header |
| Sidebar | `src/components/layout/Sidebar.tsx` | Collapsible navigation (⌘B) |
| StatusBar | `src/components/layout/StatusBar.tsx` | Live WS telemetry + privacy/cost footer |
| ChatPane | `src/components/chat/ChatPane.tsx` | Research chat with streaming messages |
| MessageCard | `src/components/chat/MessageCard.tsx` | User/assistant message with source chips |
| AgentTimeline | `src/components/chat/AgentTimeline.tsx` | Live plan → execute → verify steps |
| BrowserPane | `src/components/browser/BrowserPane.tsx` | iframe sandbox with tabs + URL bar |
| KnowledgeMini | `src/components/knowledge/KnowledgeMini.tsx` | Lightweight 2D knowledge graph |
| InspectorPanel | `src/components/inspector/InspectorPanel.tsx` | Context-aware right panel |
| PrivacyControls | `src/components/privacy/PrivacyControls.tsx` | 4-mode selector + report |
| AuditLogViewer | `src/components/audit/AuditLogViewer.tsx` | Live audit stream |
| VaultUnlock | `src/components/vault/VaultUnlock.tsx` | Vault unlock form |
| MemoryPanel | `src/components/memory/MemoryPanel.tsx` | Profile/recall/sessions/store |
| CommandPalette | `src/components/command/CommandPalette.tsx` | ⌘K cmdk palette |
| OnboardingWizard | `src/components/onboarding/OnboardingWizard.tsx` | First-run + help reopen |

---

## Backend Modules

| Module | File | Description |
|--------|------|-------------|
| Engine | `backend/engine.py` | FastAPI app + middleware wiring (254 lines) |
| Engine Runtime | `backend/engine_runtime.py` | `ConnectionManager`, `safe_task`, broadcast helpers, LLM config |
| Routes | `backend/routes/` | 20 domain-specific route modules |
| Database | `backend/core/database.py` | SQLite + migrations |
| Privacy | `backend/core/privacy.py` | PII detection + network isolation |
| Audit | `backend/core/audit.py` | Tamper-evident logging (uses shared PIIDetector) |
| Vault | `backend/core/vault.py` | AES-256-GCM credential storage |
| Vector Search | `backend/core/vector_search.py` | sqlite-vec / numpy fallback |
| Security | `backend/core/security.py` | `is_safe_url`, `safe_filename`, `is_safe_path` |
| Security Headers | `backend/core/security_headers.py` | CSP, HSTS, X-Frame-Options middleware |
| Body Size Limit | `backend/core/body_size_limit.py` | 2 MB request body cap |
| Trusted Host | `backend/core/trusted_host.py` | Host header validation |
| Request ID | `backend/core/request_id.py` | 12-char correlation ID middleware |
| Request Timeout | `backend/core/request_timeout.py` | 30 s request timeout |
| Access Log | `backend/core/access_log.py` | Structured access log middleware |
| Security Events | `backend/core/security_events.py` | Centralised blocked-request audit |
| Rate Limiter | `backend/core/rate_limiter.py` | Token-bucket rate limiting |
| Supply Chain | `backend/core/supply_chain.py` | Dependency integrity verification |
| Search | `backend/modules/search.py` | Multi-engine search |
| Browser | `backend/modules/browser.py` | Session isolation + privacy |
| Fingerprint | `backend/modules/fingerprint_rotator.py` | Browser fingerprint rotation |
| Knowledge Graph | `backend/modules/knowledge_graph.py` | Entity-relation graph |
| Missions | `backend/modules/missions.py` | Cron-based scheduler |
| Consensus | `backend/modules/consensus_engine.py` | Multi-node voting |
| Supply Chain | `backend/core/supply_chain.py` | Dependency verification |
| MLX Provider | `backend/modules/mlx_provider.py` | Apple Silicon MLX integration, model registry, server lifecycle |
| MLX VLM Server | `backend/scripts/mlx_vlm_server.py` | OpenAI-compatible FastAPI server for Gemma 3 via mlx-vlm |
| **LLM Layer (v3)** | `backend/llm/` | Unified provider abstraction: 6 providers, registry, routing, cost estimation |
| **Agent Loop (v3)** | `backend/agent/` | ReAct/Plan-Execute loop with tool registry, verification, replanning, SSE events |
| **Memory (v3)** | `backend/memory/` | 4-store memory system: user profile, session, semantic (with embeddings), procedural |
| **V2 Endpoints (v3)** | `backend/engine.py:36xx+` | 16 new `/v2/*` endpoints: LLM chat, agent run, memory CRUD + recall |

---

## Testing

```bash
# Run the full suite (prints the current pass/fail count — the suite grows
# continuously, so run it rather than trusting any hardcoded number)
python3 -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_real_llm_integration.py \
                   --ignore=tests/test_search_integration.py --ignore=tests/test_socks.py \
                   -v --tb=short

# Frontend build + typecheck + lint + test (104 Vitest tests)
cd browser-app && npm run build && npm run typecheck && npm run lint && npm test
```

The CI workflow (`.github/workflows/test.yml`) runs all passing test categories on every push:
core backend, LLM layer, memory, agent loop, security middleware stack (9 files),
engine runtime, MCP server (stdio), eval, CLI, AI employees (6 specialist auditors),
and more. Tests requiring live services (E2E, real LLM, SearXNG, SOCKS proxy)
are excluded from CI — run those manually when the corresponding service is up.

---

## Development Scripts

Hand-rolled scripts in `scripts/` make the dev loop one command:

```bash
./scripts/dev.sh                 # backend + LLM + Tauri in one shot
./scripts/build.sh               # production build for current platform
./scripts/build.sh --skip-signing  # unsigned (local testing)
./scripts/sign.sh <path-to-.app>   # sign + notarize an existing macOS build
./scripts/gen-updater-keys.sh    # generate the Tauri auto-updater keypair
```

See `scripts/dev.sh` for the full menu, or just run with `--help`.

---

## Tauri Desktop App

The Tauri 2 shell in `browser-app/` wraps the React frontend with a Rust
orchestrator. It spawns the Python backend and llama-server sidecar on first
launch, handles `jambubrowser://` deep links, and ships native auto-updates.

```bash
cd browser-app
npm install
npm run tauri dev          # dev mode
npm run tauri build        # release build
```

**Code signing (macOS):** set the `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
`APPLE_PASSWORD`, `APPLE_TEAM_ID` env vars (see `.env.example`), then run
`./scripts/build.sh`. The release workflow in `.github/workflows/release.yml`
does this automatically on `v*` tags.

**Auto-updates:** generate a keypair with `./scripts/gen-updater-keys.sh` and
paste the public key into `browser-app/src-tauri/tauri.conf.json` under
`plugins.updater.pubkey`. The private key stays in CI secrets as
`TAURI_SIGNING_PRIVATE_KEY`.

**CI/CD:** see `.github/workflows/` for `test.yml` (every push) and `release.yml`
(multi-platform matrix on tags). Dependabot is configured in
`.github/dependabot.yml` for weekly dependency updates.

See `browser-app/README.md` for the full Tauri-specific documentation.

---

## License

MIT — see [LICENSE](LICENSE).

Built for digital freedom by [pmaero-byte](https://github.com/pmaero-byte).
