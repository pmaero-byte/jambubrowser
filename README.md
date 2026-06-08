# Jambubrowser

**The Sovereign Autonomous Research Agent**

Jambubrowser is a fully local, privacy-first autonomous AI browser and research engine. It thinks, acts, and evolves entirely on your machine. Built for researchers, security professionals, and power users who need unconstrained internet access with absolute forensic safety.

---

## Quick Start

### Prerequisites
- Python 3.9+
- Node.js 18+
- Ollama (for local LLM)

### Backend
```bash
cd backend
pip install -r requirements.txt
python3 -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001
```

### Frontend
```bash
cd frontend/jambubrowser-ui
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

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
- **LLM Synthesis**: Local (Ollama/Gemma4) + Cloud (MiniMax) support
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

---

## Testing

```bash
# Run all 52 tests
python3 -m pytest tests/test_backend.py tests/test_e2e.py -v

# Unit tests only (22)
python3 -m pytest tests/test_backend.py -v

# E2E tests only (30, requires running backend)
python3 -m pytest tests/test_e2e.py -v

# Frontend build
cd frontend/jambubrowser-ui && npm run build
```

---

## License

Built for digital freedom by [pmaero-byte](https://github.com/pmaero-byte).
