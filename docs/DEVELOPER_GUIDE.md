# Developer Guide

## Project Structure

```
jambubrowser/
├── backend/
│   ├── engine.py              # FastAPI app factory (~320 lines): middleware + router includes
│   ├── routes/                # Domain route modules (research, browser, vault, knowledge, ...)
│   ├── engine_runtime.py      # Shared runtime state (ConnectionManager, broadcast helpers)
│   ├── core/
│   │   ├── database.py        # SQLite + migrations + memory
│   │   ├── privacy.py         # PII detection + network isolation
│   │   ├── audit.py           # Tamper-evident logging
│   │   ├── vault.py           # AES-256-GCM credential storage
│   │   ├── vector_search.py   # sqlite-vec / numpy fallback
│   │   ├── sandbox.py         # Sandboxed code execution
│   │   ├── rate_limiter.py    # Per-endpoint rate limiting
│   │   ├── supply_chain.py    # Dependency verification
│   │   └── llm_config.py      # LLM provider configuration
│   └── modules/
│       ├── search.py          # Multi-engine search
│       ├── browser.py         # Session isolation + privacy
│       ├── scraper.py         # Crawl4ai scraping
│       ├── playwright_scraper.py  # Playwright fallback
│       ├── fingerprint_rotator.py # Browser fingerprint rotation
│       ├── knowledge_graph.py # Entity-relation graph
│       ├── missions.py        # Cron-based scheduler
│       ├── consensus_engine.py # Multi-node voting
│       ├── shadow_browser.py  # Autonomous browsing
│       ├── risk_shield.py     # URL risk assessment
│       ├── vision.py          # Computer vision
│       ├── form_filler.py     # Auto form filling
│       ├── local_connector.py # macOS integration
│       ├── multimodal_input.py # Image/file/text processing
│       ├── skill_synthesizer.py # Auto skill creation
│       ├── notifications.py   # System notifications
│       ├── model_manager.py   # LLM model management
│       ├── p2p_discovery.py   # Peer discovery
│       ├── federated_rag.py   # Federated RAG
│       ├── harness_bridge.py  # Harness integration
│       └── youtube.py         # YouTube analysis
├── browser-app/               # Tauri 2 desktop app + web UI (React 19 + Vite + Tailwind)
│   ├── src/
│   │   ├── App.tsx            # AppShell layout + lazy-loaded panels
│   │   ├── main.tsx           # Entry point
│   │   ├── components/        # Feature panels (chat, browser, audit, vault, knowledge, ...)
│   │   │   └── layout/        # AppShell, Sidebar, TopBar, StatusBar
│   │   ├── store/             # Zustand app state
│   │   └── utils/
│   │       ├── api.ts         # HTTP helpers
│   │       └── useAgentWebSocket.ts  # Agent state hook
│   ├── src-tauri/             # Rust shell: orchestrator, deep links, auto-updater
│   ├── package.json
│   └── vite.config.ts
├── tests/
│   ├── test_backend.py        # 22 unit tests
│   └── test_e2e.py            # 30 E2E tests
├── docs/
│   ├── API.md                 # Complete API reference
│   ├── ARCHITECTURE.md        # Technical architecture
│   ├── USER_GUIDE.md          # User guide
│   ├── FEATURES.md            # Feature map
│   └── DEVELOPER_GUIDE.md     # This file
└── models/
    └── gemma-3-12b-it-Q4_K_M.gguf  # Local LLM
```

## Setup

### Prerequisites
- Python 3.9+ (Apple's system Python works)
- Node.js 18+
- Ollama (for local LLM)
- Playwright (optional, for browser automation)

### Backend Setup
```bash
# Install dependencies
pip install fastapi uvicorn pydantic httpx psutil \
    cryptography sentence-transformers numpy \
    playwright crawl4ai markdownify sqlite-vec websockets

# Start backend
python3 -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001
```

### Frontend Setup
```bash
cd browser-app
npm install
npm run dev    # Development server on port 1420
npm run build  # Production build to dist/
```

### LLM Setup
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull Gemma 3 model
ollama pull gemma3:4b

# Start Ollama
ollama serve
```

## Development Workflow

### Running Tests
```bash
# All 52 tests
python3 -m pytest tests/test_backend.py tests/test_e2e.py -v

# Unit tests only (no backend required)
python3 -m pytest tests/test_backend.py -v

# E2E tests (backend must be running)
python3 -m pytest tests/test_e2e.py -v
```

### Code Style
- Backend: Python 3.9 compatible (no `X | Y` union syntax)
- Frontend: TypeScript strict mode
- No comments unless requested
- Follow existing patterns in neighboring files

### Adding a New Endpoint

1. Add request model and endpoint to the relevant module in `backend/routes/`
   (or create a new one — see `backend/routes/system.py` for the pattern):
```python
class MyRequest(BaseModel):
    field: str
    optional_field: str = ""

@router.post("/my/endpoint")
async def my_endpoint(req: MyRequest):
    # Implementation
    return {"result": "success"}
```

2. If you created a new route module, include its router in `backend/engine.py`.

3. Add to Vite proxy in `browser-app/vite.config.ts` (if not already covered):
```typescript
'/my': { target: 'http://localhost:8001', changeOrigin: true },
```

4. Add test in `tests/test_e2e.py`:
```python
class TestMyEndpoint:
    def test_my_endpoint(self, client):
        resp = client.post("/my/endpoint", json={"field": "test"})
        assert resp.status_code == 200
```

### Adding a New Frontend Component

1. Create component in `browser-app/src/components/<feature>/MyComponent.tsx`:
```tsx
export function MyComponent() {
    return <div className="...">...</div>;
}
```

2. Import and use in `App.tsx` (large panels are lazy-loaded — follow the
   existing `lazy(() => import(...))` pattern):
```tsx
import { MyComponent } from "./components/<feature>/MyComponent";
```

3. Style with Tailwind utility classes (the v4 UI does not use a monolithic `App.css`).

### Adding a New Backend Module

1. Create module in `backend/modules/my_module.py`:
```python
class MyModule:
    def __init__(self):
        self._data = {}

    def do_something(self):
        return {"result": "success"}

_module = None

def get_my_module() -> MyModule:
    global _module
    if _module is None:
        _module = MyModule()
    return _module
```

2. Import in the relevant `backend/routes/` module and add an endpoint:
```python
from backend.modules.my_module import get_my_module

@router.get("/my/module")
async def my_module_endpoint():
    return get_my_module().do_something()
```

## Configuration

### Environment Variables
```bash
# LLM Provider
OLLAMA_BASE_URL=http://localhost:11434/v1
MINIMAX_API_KEY=your_key_here

# Database
JAMBU_DB_PATH=rag_data.db

# Vault
JAMBU_MASTER_PASSWORD=your_password
JAMBU_VAULT_TIMEOUT=300  # seconds

# Privacy
AGENT_VPN_PROXY=socks5://127.0.0.1:9050

# Search
SEARXNG_URL=http://localhost:8888/search
```

### Vite Proxy Configuration
The frontend dev server proxies all backend paths:
```typescript
// vite.config.ts
proxy: {
    '/health': { target: 'http://localhost:8001', changeOrigin: true },
    '/research': { target: 'http://localhost:8001', changeOrigin: true },
    '/ws': { target: 'http://localhost:8001', changeOrigin: true, ws: true },
    // ... 30+ routes
}
```

## Database Migrations

Migrations are handled in `database.py` during `init_db()`:
```python
# Add column if missing
try:
    cursor.execute("SELECT next_run FROM missions LIMIT 1")
except sqlite3.OperationalError:
    cursor.execute("ALTER TABLE missions ADD COLUMN next_run REAL")
```

## Performance Tips

- **Embedding Cache**: Reuses cached embeddings for identical text chunks
- **Vector Search Fallback**: Falls back to numpy when sqlite-vec unavailable
- **HTTP Connection Pooling**: Uses httpx.AsyncClient for connection reuse
- **WebSocket Reconnection**: Auto-reconnects every 3 seconds
- **Fetch Timeout**: 30-second AbortController timeout on all API calls

## Troubleshooting

### "No supported WebSocket library"
```bash
pip install websockets
```

### "Ollama not available"
```bash
ollama serve  # Start Ollama on port 11434
```

### "sqlite-vec not available"
The app falls back to numpy cosine similarity automatically. For better performance:
```bash
pip install sqlite-vec
```

### "Port 8001 already in use"
```bash
lsof -ti:8001 | xargs kill -9
```
