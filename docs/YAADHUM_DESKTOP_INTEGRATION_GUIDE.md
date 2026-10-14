# YaadhumAI & JambuBrowser Integration Guide

This guide details the sovereign integration between **JambuBrowser Engine** (`browser_project`) and **YaadhumAI Desktop App** (`yaadhum_ncp`).

---

## 1. Architectural Topology

```
+-----------------------------------------------------------------------------------+
|                            YaadhumAI macOS Desktop App                            |
|                            (/Users/prabaharan/yaadhum_ncp)                         |
|                                                                                   |
|  Tab 1: Sovereign Chat  |  Tab 2: Radiography  |  Tab 3: JambuBrowser  |  Tab 4: Kaniyan  |
+-------------------------------------------------------+---------------------------+
                                                        |
                    Single-Round-Trip JSON API / Native NSImage / Ref Action
                                                        |
                                                        v
+-----------------------------------------------------------------------------------+
|                        JambuBrowser Engine (Port 8001 / 8011)                     |
|                        (/Users/prabaharan/My_projects/browser_project)             |
|                                                                                   |
|  - Headless Playwright / Chromium Sandbox                                         |
|  - Semantic Accessibility Tree (@ref badges)                                      |
|  - High-Efficiency JPEG Viewport Screencaster                                     |
|  - Local Workspace & file:// Protocol Confinement                                |
|  - Deterministic Security & Privacy Audit Engine                                  |
+--------------------+----------------------------------+---------------------------+
                     |                                  |
                     v                                  v
+------------------------------------+  +-------------------------------------------+
|    Greenwich LLM Stack             |  |        Permanent Memory Bridge            |
|    - Port 8085: Safe Qwen 3.8 27B  |  |        - Port 8095: Gated DeltaNet SSM    |
|    - Port 8090: Quantum Daemon     |  |        - 828M+ Ingested Tokens            |
+------------------------------------+  +-------------------------------------------+
```

---

## 2. Dedicated High-Performance Desktop Endpoints

Located in `backend/routes/browser_desktop.py` and mounted in `backend/engine.py`.

### A. Health & Multi-Daemon Status
- **`GET /browser/desktop/status`** (Alias: `/browser/yaadhum/status`)
- Returns the health of the browser engine, local workspaces, Greenwich LLM (`:8085` / `:8090`), and Permanent Memory Bridge (`:8095`).
```json
{
  "ok": true,
  "engine": {
    "name": "Jambubrowser Engine",
    "version": "3.4.0",
    "port": 8001,
    "active_sessions": 0
  },
  "local_workspaces": {
    "allowed": true,
    "workspace_roots": ["/Users/prabaharan", "/tmp"]
  },
  "memory_bridge": {
    "url": "http://127.0.0.1:8095",
    "ok": true,
    "details": {
      "total_tokens_ingested": 828707714,
      "model_id": "qwen3.8-27b-hybrid",
      "status": "active"
    }
  },
  "greenwich_llm": {
    "primary_url": "http://127.0.0.1:8085/v1",
    "primary_online": true,
    "default_model": "qwen3.8-27b"
  }
}
```

### B. Single-Round-Trip Inspection
- **`POST /browser/desktop/inspect`**
- Eliminates multi-round-trip latency by navigating, generating the semantic AX tree, extracting clean readability markdown, capturing a compressed JPEG frame for visual rendering, and optionally auto-syncing to the Memory Bridge in one call.
- **Request**:
```json
{
  "url": "file:///Users/prabaharan/yaadhum_ncp/openmodels/webapp/brave_dom_dump.html",
  "capture_frame": true,
  "auto_sync_memory": true
}
```
- **Response**:
```json
{
  "ok": true,
  "session_id": "sess-a9b8c7d6e5",
  "url": "file:///Users/prabaharan/yaadhum_ncp/openmodels/webapp/brave_dom_dump.html",
  "title": "ReverseAI Studio",
  "ax_tree": "[1] button 'Home'\n[2] textbox 'Search' value=''\n[3] button 'Submit'",
  "ax_count": 11,
  "focused_ref": 1,
  "text": "# Clean Markdown Page Content...",
  "frame_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
  "latency_ms": 320,
  "memory_synced": true
}
```

### C. Ref & Coordinate Action
- **`POST /browser/desktop/act`**
- Executes element actions (`click`, `type`, `press`, `hover`) targeting either an accessibility `@ref` or viewport coordinates `(x, y)` and returns the updated AX tree and viewport frame immediately.
- **Request**:
```json
{
  "session_id": "sess-a9b8c7d6e5",
  "action": "click",
  "ref": 1
}
```

### D. Privacy, Security & PII Audit
- **`POST /browser/desktop/audit`**
- Runs static and dynamic inspection for tracking scripts, sensitive query parameters (API keys, session tokens), plain text email/phone leakage, and insecure transport.
- Auto-syncs audit findings to Memory Bridge at `:8095` when requested.

### E. Tamil Siddha & Scholarly Research
- **`POST /browser/desktop/research`**
- Synthesizes findings using local Qwen 3.8 27B, specifically cross-referencing Agathiyar, Gnanavettiyan, alchemy (*rasavatham*), and botanical herbs, ingesting discovered citations directly into long-term SSM memory.

---

## 3. YaadhumAI macOS Client Implementation

File: `macos_app/src/JambuBrowserView.swift`

### Key Capabilities:
1. **Three-Mode Left Pane**:
   - **Mode 0 (Visual Viewport)**: Native `NSImage` rendering from JPEG frame base64. Clicking inside the viewport sends normalized coordinates to `POST /browser/desktop/act`.
   - **Mode 1 (Semantic AX Tree)**: Displays interactive `@ref` badges. Clicking any badge automatically populates the action bar.
   - **Mode 2 (Clean DOM Text)**: Monospaced markdown representation of the page text with selection enabled.
2. **Interactive Action Bar**:
   - Ref target input `@ref`.
   - Action text field (supporting Unicode Tamil input).
   - "Click" and "Type & Enter" trigger buttons.
3. **Dual-Port Auto-Discovery**:
   - Probes `:8001` (standard engine port) and falls back to `:8011` (dev engine port).
   - Interactive port badge in the status bar allows instant switching and re-probing.
4. **Scholarly & Siddha Research Controls**:
   - One-click "Deep Research" streaming synthesis.
   - "Siddha Citations" extraction targeting palm-leaf manuscripts and verses.
   - "Safety & Privacy Audit" card view with circular score gauge.
   - "Sync to Memory Bridge" button for sovereign state retention.

---

## 4. Security & Workspace Policy Configuration

In `.env`:
```bash
# Allow browsing local file:// schemes and loopback daemons
ALLOW_LOCAL_WORKSPACES=true
JAMBU_ALLOW_LOCAL_WORKSPACES=true
JAMBU_LOCAL_WORKSPACE_ROOTS=/Users/prabaharan,/tmp

# Memory Bridge connection
GREENWICH_MEMORY_URL=http://127.0.0.1:8095

# LLM Fallback Chain (Greenwich first)
JAMBU_LLM_FALLBACK_CHAIN=greenwich,anthropic,openai,groq
```

---

## 5. Verification & Testing

### Run Backend Unit & Integration Tests:
```bash
.venv/bin/pytest tests/test_desktop_endpoints.py tests/test_greenwich_bridge.py -v
```

### Recompile YaadhumAI Desktop App:
```bash
cd /Users/prabaharan/yaadhum_ncp/macos_app && ./build_app.sh
```

### Launch YaadhumAI App:
```bash
open "/Users/prabaharan/yaadhum_ncp/YaadhumAI.app"
```
Navigate to **Tab 3 ("JambuBrowser")** to browse local files or web pages with sovereign memory integration.
