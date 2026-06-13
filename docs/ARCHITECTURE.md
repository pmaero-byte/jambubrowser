# Technical Architecture

## System Overview

Jambubrowser is a three-layer architecture designed for privacy-first autonomous research.

```
Frontend (React + Vite)  ←→  Backend (FastAPI)  ←→  Storage (SQLite + sqlite-vec)
         port 5173                    port 8001              rag_data.db
```

### Security Middleware (v3.3.0+)

Every HTTP request flows through this middleware stack (outermost
first). Each middleware is pure ASGI and lives under `backend/core/`.

| Middleware | Purpose |
|---|---|
| `AccessLogMiddleware` | One structured log line per request |
| `RequestIDMiddleware` | 12-char hex correlation ID (reuses client `X-Request-ID`) |
| `TrustedHostMiddleware` | Reject untrusted `Host` headers (DNS rebinding + Host injection) |
| `SecurityHeadersMiddleware` | CSP, HSTS, X-Frame-Options, Permissions-Policy |
| `RequestTimeoutMiddleware` | Cancel requests > 30 s (excludes long-running paths) |
| `GZipMiddleware` | Compress responses ≥ 500 bytes |
| `BodySizeLimitMiddleware` | Reject bodies > 2 MB |
| `RateLimitMiddleware` | Per-IP + per-endpoint token bucket |

SSRF protection runs at the Pydantic layer: every URL-accepting
endpoint validates through `is_safe_url()` (blocks private IPs,
localhost, link-local, non-HTTP schemes). See `SECURITY.md` for the
full threat model.

### Three Pillars (v3.0.0)

The backend was reorganized in v3.0.0 around three core modules that back the `/v2/*` endpoints:

- **`backend/llm/`** — Unified LLM provider layer. One `Provider` protocol, six implementations (Anthropic, OpenAI, Ollama, MLX, MiniMax, Mock), registry + routing.
- **`backend/agent/`** — ReAct/Plan-Execute loop. Tool registry with auto-derived JSON Schema, plan/verify/replan cycle, SSE event stream.
- **`backend/memory/`** — Persistent identity + knowledge. Four sub-stores (user profile, session, semantic, procedural), hybrid retrieval.

The legacy `engine.py` (60+ endpoints) is preserved and unchanged in behavior; new code routes through the three pillars via thin shims.

In v3.3.0, the 176 route handlers were extracted from `engine.py`
into 20 domain-specific modules under `backend/routes/` (research,
browser, vault, knowledge, memory, missions, tools, etc.). Shared
runtime state (`ConnectionManager`, `safe_task`, broadcast helpers,
LLM config) lives in `backend/engine_runtime.py`. The application
factory `backend/engine.py` is now ~250 lines and only handles
middleware registration and router includes.

## Backend Architecture

### FastAPI Application (`engine.py`)

The backend is a single FastAPI application serving 76+ endpoints across these categories:

- **System**: Health, stats, model management
- **Research**: Query expansion, multi-engine search, LLM synthesis (with `use_agent` opt-in)
- **Browser**: Scraping (crawl4ai → Playwright fallback), automation
- **Privacy**: Mode management, URL checking, content sanitization
- **Audit**: Tamper-evident logging with SHA-256 hash chain
- **Vault**: AES-256-GCM encrypted credential storage
- **Security**: Supply chain verification, fingerprint rotation
- **Knowledge**: Entity extraction, relationship inference, graph storage
- **V2 LLM** (`/v2/llm/*`): Unified chat + provider listing
- **V2 Agent** (`/v2/agent/*`): ReAct loop execution, tool listing, run history
- **V2 Memory** (`/v2/memory/*`): Profile, sessions, semantic, procedural, recall
- **Missions**: Cron-based background research scheduler
- **Consensus**: Multi-node voting for federated decisions
- **Computer Use**: macOS screen capture, mouse/keyboard control
- **Vision**: OCR, UI element detection, screen verification
- **Multimodal**: Image, file, and text processing

### Request Flow

```
1. User submits query via frontend
2. Frontend POSTs to /research via localFetch (30s timeout)
3. Backend receives request, validates privacy mode
4. Query expansion via LLM (3 diverse queries)
5. Multi-engine search (SearXNG → DDG API → Google fallback)
6. Security screening (risk assessment per URL)
7. Content scraping (crawl4ai → Playwright → httpx fallback)
8. Vector indexing (SentenceTransformer → sqlite-vec / numpy)
9. Context ranking (cosine similarity + keyword matching)
10. LLM synthesis (Ollama local → MiniMax cloud fallback)
11. Response returned to frontend
12. WebSocket broadcasts agent state throughout
```

### Privacy Layers

```
Request → NetworkIsolator → ContentSanitizer → AuditLogger → Storage
              │                    │                │
              ▼                    ▼                ▼
         Blocks URLs          Redacts PII      Logs action
         Removes headers     Strips tracking   Hash chain
```

Four privacy modes:
- **Standard**: Basic sanitization
- **Enhanced**: Aggressive PII removal, tracking blocked
- **Maximum**: Zero external calls, full sanitization
- **Local-Only**: No network access at all

### Credential Vault

```
Master Password → PBKDF2 (480k iterations) → Fernet Key → AES-256-GCM
                                                       │
                                    Machine-specific salt (~/.jambu/vault.salt)
```

- Per-credential unique nonce
- Auto-lock after 5 minutes of inactivity
- 5 failed attempts → 5 minute lockout
- Secure memory handling (zeroing on delete)

### Audit Chain

```
Entry N-1.hash → Entry N.hash = SHA256(entry_data + previous_hash)
```

Each audit entry includes:
- Timestamp, category, action, details
- Actor, session_id
- SHA-256 hash (chained to previous entry)
- PII automatically redacted from details

### Vector Search

Two-tier search:
1. **sqlite-vec** (if available): Direct vector similarity search in SQL
2. **numpy fallback**: Load all embeddings, compute cosine similarity in Python

Embedding model: `all-MiniLM-L6-v2` (384 dimensions, ~80MB)

### WebSocket Protocol

Two WebSocket endpoints:

**`/ws/{client_id}`** - Agent state broadcasts:
```json
{"type": "agent.state", "state": "thinking", "zone": "pile", "task_id": "abc123"}
{"type": "agent.telemetry", "model": "gemma4:12b-it-qat", "tokens_per_sec": 42.5}
{"type": "agent.reasoning", "delta": "Based on the research..."}
{"type": "agent.task_start", "task_id": "abc123", "query": "What is..."}
{"type": "agent.task_end", "task_id": "abc123", "status": "completed"}
```

**`/ws/audit`** - Live audit log updates:
```json
{"type": "stats", "data": {"total_entries": 8, "by_category": {...}}}
```

## Frontend Architecture

### Component Tree

```
App
├── Header (Navigation tabs, Full Power toggle)
├── AgentStatusBar (WebSocket-powered live state)
└── Main Layout (split-view)
    ├── Sidebar (30%)
    │   ├── MetricsPanel (nodes, tokens, RAM)
    │   ├── Welcome / MessageList (chat messages)
    │   └── CommandBar (input, domain selector)
    └── Browser Area (70%)
        ├── TabSystem (multi-tab management)
        ├── BrowserPane (iframe / blank placeholder)
        └── Overlays (AnimatePresence)
            ├── PrivacyControls
            ├── AuditLogViewer
            ├── VaultUnlock
            └── History Panel
```

### State Management

All state lives in `App.tsx` via React hooks:
- `tabs[]` - Browser tab state
- `activeTabId` - Currently active tab
- `messages[]` - Chat message history
- `input` - Current input text
- `isLoading` - Research in progress
- `fullPower` - Brain-only vs full research mode
- `activeTab` - Active overlay (chat/privacy/audit/vault)
- `metrics` - Performance metrics
- `history[]` - Browser visit history

### API Communication

```typescript
// api.ts - HTTP requests with 30s timeout
localFetch("/research", { method: "POST", body: JSON.stringify({...}) })

// useAgentWebSocket.ts - Real-time agent state
const { connected, agentState, telemetry } = useAgentWebSocket()
```

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Cmd+K | Focus input field |
| Cmd+P | Open Privacy tab |
| Cmd+L | Open Audit tab |
| Cmd+1 | Return to Research tab |
| Cmd+T | New browser tab |
| Escape | Close overlay, return to chat |

## Database Schema

```sql
-- Core research storage
documents (id, url, text, created_at)
vec_documents (id, embedding)  -- sqlite-vec virtual table
embedding_cache (hash, embedding)

-- Credential vault
credential_vault (id, domain, url_pattern, username, password_encrypted, metadata, last_used)

-- Missions
missions (id, query, status, last_run, next_run, schedule)

-- Consensus
proposals (id, title, description, options_json, required_nodes, status, winner)
votes (id, proposal_id, node_id, choice, confidence, reasoning)

-- Browser sessions
browser_sessions (id, name, cookies, local_storage, user_agent, proxy)

-- Memory (Harness-compatible)
memory_entries (id, category, key, value, importance, access_count)
memory_fts (category, key, value)  -- FTS5 virtual table

-- Analytics
task_metrics (id, endpoint, method, status, duration_ms, timestamp)
tool_usage (id, tool_name, call_count, success_count, total_duration_ms)
sessions (id, name, task_count, total_duration_ms)
```

## Performance

- **Backend startup**: ~2 seconds
- **Health check**: <10ms
- **Search (DDG)**: 1-3 seconds
- **Research (brain-only)**: <1 second
- **Research (full)**: 5-15 seconds
- **Frontend build**: 275 KB JS, 9 KB CSS
- **Unit tests**: 0.8s (22 tests)
- **E2E tests**: 15s (30 tests)
