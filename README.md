# Jambubrowser

**The Sovereign Autonomous Research Agent**

Jambubrowser is a fully local, privacy-first autonomous AI browser and research engine. It thinks, acts, and evolves entirely on your machine. Built for researchers, security professionals, and power users who need unconstrained internet access with absolute forensic safety.

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

# Start the MLX VLM server with Gemma 4 12B (auto-downloads from HuggingFace)
mlx-venv/bin/python3 backend/scripts/mlx_vlm_server.py \
  --model mlx-community/gemma-4-12B-it-4bit \
  --port 8080
```

Then send research requests with `"llm_provider": "mlx"`.

### Frontend
```bash
cd frontend/jambubrowser-ui
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

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
export JAMBU_LLM_ANTHROPIC_MODEL="claude-sonnet-4-6"
export JAMBU_LLM_OPENAI_MODEL="gpt-4o"
export JAMBU_LLM_OLLAMA_MODEL="gemma4:12b-it-qat"
export JAMBU_LLM_OPENAI_BASE_URL="https://api.openai.com/v1"  # or vLLM, Together, etc.

# Then in the CommandBar, pick a provider from the dropdown, or use "auto".
```

V2 endpoints exposed by the running engine:
- `POST /v2/llm/chat` — unified chat (any provider, optional streaming)
- `POST /v2/agent/run` — ReAct/Plan-Execute loop with SSE events
- `GET/POST /v2/memory/*` — 4-store memory CRUD + hybrid recall

### Test
```bash
# Unit tests (22 tests)
python3 -m pytest tests/test_backend.py -v

# E2E tests (30 tests, requires running backend)
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
                      │ HTTP + WebSocket
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
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                     SQLite + sqlite-vec                      │
│  documents | vec_documents | missions | credential_vault    │
│  proposals | votes | browser_sessions | memory_entries      │
│  task_metrics | tool_usage | provider_quota | sessions      │
└─────────────────────────────────────────────────────────────┘
```

---

## Features

### Agentic Research (v3 — the three pillars)
- **Unified LLM Layer**: 6 providers (Anthropic, OpenAI, Ollama, MLX, MiniMax, Mock) behind one `Provider` protocol. Auto-discovery, env-driven defaults, smart routing (`cheapest` / `fastest` / `quality` / `fallback` / `local_only`), per-request cost tracking.
- **ReAct Agent Loop**: Plan → Execute → Verify → Replan, with streaming SSE events. Auto-derived JSON Schema for every tool, 10 built-in tools wrapping existing capabilities (web_search, scrape_url, vault_get, knowledge_query, memory_recall, memory_store, code_exec, goal_set, risk_check, final_answer). Budget-aware (max steps / tokens / seconds).
- **Memory & Personalization**: 4 sub-stores (user profile, session, semantic with embeddings, procedural with success rates). Hybrid retrieval: 60% vector + 30% recency+importance + 10% FTS, with profile-interest boost. Per-user scoping, full forget support.

### Privacy & Security
- **4 Privacy Modes**: Standard, Enhanced, Maximum, Local-Only
- **PII Detection**: Auto-redacts emails, phones, SSNs, credit cards, IPs
- **Tracking Protection**: Blocks Google Analytics, Facebook Pixel, Mixpanel, etc.
- **Credential Vault**: AES-256-GCM encrypted with PBKDF2 (480k iterations)
- **Tamper-Evident Audit Log**: SHA-256 hash chain of all actions
- **Supply Chain Verification**: Hash verification for all Python dependencies
- **Tor Routing**: SOCKS5 proxy support for anonymous research
- **Browser Fingerprint Rotation**: Unique profiles per session

### Research & Intelligence
- **Multi-Engine Search**: SearXNG → DuckDuckGo API → Google fallback
- **Hybrid RAG Pipeline**: SQLite-vec vector search + semantic memory
- **Knowledge Graph**: Entity extraction, relationship inference, topic clustering
- **Swarm Research**: Decomposes complex queries into parallel sub-missions
- **LLM Synthesis**: Local (Ollama/MLX/Gemma4) + Cloud (MiniMax) support
- **MLX LM Provider**: Apple Silicon-native inference via `mlx-vlm` — 12-33 tok/s on M4 Pro with Gemma 4 12B, 4 privacy modes, local-first
- **Brain-Only Mode**: Search local knowledge vault without web access

### Browser & Automation
- **Multi-Tab Management**: Add, close, switch tabs
- **Playwright Integration**: Headless browser automation
- **Computer Use**: macOS screen capture, mouse/keyboard control
- **Form Filler**: Auto-fill login forms with vault credentials
- **Vision Grounding**: OCR, UI element detection, screen verification

### Frontend
- **Split-View Layout**: Chat sidebar (30%) + Browser workspace (70%)
- **Real-Time Agent Status**: WebSocket-powered live state visualization
- **Keyboard Shortcuts**: Cmd+K focus, Cmd+P privacy, Cmd+L audit, Cmd+T new tab
- **Error Boundary**: Crash protection for the entire app
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
| System | `/health` | GET | Health check |
| System | `/stats` | GET | Database statistics |
| Research | `/research` | POST | Autonomous research |
| Research | `/search` | GET | Raw metasearch |
| Browser | `/scrape` | POST | Single page scraping |
| Browser | `/act` | POST | Browser automation |
| Browser | `/login` | POST | Credential vault login |
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

| Component | File | Description |
|-----------|------|-------------|
| App | `src/App.tsx` | Main layout, state, handlers |
| Header | `src/components/Header.tsx` | Navigation tabs + Full Power toggle |
| CommandBar | `src/components/CommandBar.tsx` | Input with domain selector |
| MessageList | `src/components/MessageList.tsx` | Chat messages with source chips |
| BrowserPane | `src/components/BrowserPane.tsx` | Web view with URL bar |
| TabSystem | `src/components/TabSystem.tsx` | Multi-tab management |
| MetricsPanel | `src/components/MetricsPanel.tsx` | Live performance metrics |
| AgentStatusBar | `src/components/AgentStatusBar.tsx` | WebSocket agent state |
| PrivacyControls | `src/components/PrivacyControls.tsx` | Privacy mode selector |
| AuditLogViewer | `src/components/AuditLogViewer.tsx` | Live audit log display |
| VaultUnlock | `src/components/VaultUnlock.tsx` | Vault unlock interface |
| Welcome | `src/components/Welcome.tsx` | Welcome screen |
| ErrorBoundary | `src/components/ErrorBoundary.tsx` | Crash protection |

---

## Backend Modules

| Module | File | Description |
|--------|------|-------------|
| Engine | `backend/engine.py` | FastAPI app + all endpoints |
| Database | `backend/core/database.py` | SQLite + migrations |
| Privacy | `backend/core/privacy.py` | PII detection + network isolation |
| Audit | `backend/core/audit.py` | Tamper-evident logging |
| Vault | `backend/core/vault.py` | AES-256-GCM credential storage |
| Vector Search | `backend/core/vector_search.py` | sqlite-vec / numpy fallback |
| Search | `backend/modules/search.py` | Multi-engine search |
| Browser | `backend/modules/browser.py` | Session isolation + privacy |
| Fingerprint | `backend/modules/fingerprint_rotator.py` | Browser fingerprint rotation |
| Knowledge Graph | `backend/modules/knowledge_graph.py` | Entity-relation graph |
| Missions | `backend/modules/missions.py` | Cron-based scheduler |
| Consensus | `backend/modules/consensus_engine.py` | Multi-node voting |
| Supply Chain | `backend/core/supply_chain.py` | Dependency verification |
| MLX Provider | `backend/modules/mlx_provider.py` | Apple Silicon MLX integration, model registry, server lifecycle |
| MLX VLM Server | `backend/scripts/mlx_vlm_server.py` | OpenAI-compatible FastAPI server for Gemma 4 via mlx-vlm |
| **LLM Layer (v3)** | `backend/llm/` | Unified provider abstraction: 6 providers, registry, routing, cost estimation |
| **Agent Loop (v3)** | `backend/agent/` | ReAct/Plan-Execute loop with tool registry, verification, replanning, SSE events |
| **Memory (v3)** | `backend/memory/` | 4-store memory system: user profile, session, semantic (with embeddings), procedural |
| **V2 Endpoints (v3)** | `backend/engine.py:36xx+` | 16 new `/v2/*` endpoints: LLM chat, agent run, memory CRUD + recall |

---

## Testing

```bash
# Run all 183 tests
python3 -m pytest tests/test_backend.py tests/test_engine.py tests/test_e2e.py \
                   tests/test_llm_layer.py tests/test_memory_system.py tests/test_agent_loop.py -v

# Unit tests (22)
python3 -m pytest tests/test_backend.py -v

# LLM layer (28)
python3 -m pytest tests/test_llm_layer.py -v

# Memory system (25)
python3 -m pytest tests/test_memory_system.py -v

# Agent loop (25)
python3 -m pytest tests/test_agent_loop.py -v

# E2E tests (30, requires running backend)
python3 -m pytest tests/test_e2e.py -v

# Frontend build
cd frontend/jambubrowser-ui && npm run build
```

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

Built for digital freedom by [pmaero-byte](https://github.com/pmaero-byte).
