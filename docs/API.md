# Jambubrowser API Reference

**Base URL:** `http://localhost:8001`

All endpoints accept and return JSON. WebSocket endpoints use `ws://localhost:8001`.

---

## Table of Contents

- [System](#system)
- [Research](#research)
- [Browser](#browser)
- [Privacy](#privacy)
- [Audit](#audit)
- [Vault](#vault)
- [Security](#security)
- [Fingerprint](#fingerprint)
- [Knowledge Graph](#knowledge-graph)
- [Missions](#missions)
- [Consensus](#consensus)
- [Vision](#vision)
- [Computer Use](#computer-use)
- [Multimodal](#multimodal)
- [WebSocket](#websocket)

---

## System

### GET /health

System health with real-time metrics.

**Response:**
```json
{
  "status": "online",
  "message": "Jambubrowser v2.0 is ready.",
  "ram_used_gb": 16.5,
  "ram_total_gb": 48.0,
  "cpu_percent": 27.4
}
```

### GET /stats

Database and system statistics.

**Response:**
```json
{
  "doc_count": 26,
  "active_missions": 2,
  "custom_tools": 1,
  "credentials": 0,
  "browser_sessions": 0
}
```

### GET /models/available

List available Gemma 4 models.

### GET /models/installed

List all installed models across providers.

### GET /models/status

Get status of a specific model.

**Parameters:**
- `model` (string, optional): Model name

### POST /models/pull

Pull a model via Ollama.

**Parameters:**
- `model` (string, optional): Model name (default: gemma4:12b)

### GET /models/recommend

Get recommended model based on available RAM.

### POST /models/setup

One-click Gemma 4 setup.

**Parameters:**
- `model_size` (string, optional): "7b" or "12b" (default: "12b")

### GET /models/providers

Check which LLM providers are available.

**Response:**
```json
{
  "ollama": true,
  "llamacpp": false,
  "recommended": "ollama"
}
```

### GET /llm/config

Get current LLM configuration.

---

## Research

### POST /research

Autonomous research with multi-engine search and LLM synthesis.

**Request:**
```json
{
  "query": "What is quantum computing?",
  "top_n": 5,
  "client_id": "default",
  "persist": false,
  "stealth": {},
  "domain": "general",
  "brain_only": false,
  "tor_routing": false,
  "incognito": false,
  "llm_config": {},
  "llm_provider": "ollama"
}
```

**Response:**
```json
{
  "answer": "Quantum computing uses qubits that can exist in multiple states...",
  "sources": ["https://example.com", "https://arxiv.org/abs/1234.5678"],
  "doc_count": 5,
  "context": "Full context text used for synthesis..."
}
```

**Domain options:**
- `general` - Standard web search
- `academic` - ArXiv papers
- `coding` - GitHub repositories

### GET /search

Raw metasearch without scraping.

**Parameters:**
- `q` (string, required): Search query
- `engines` (string, optional): Comma-separated engines (default: "google,bing,duckduckgo")

**Response:**
```json
{
  "results": [
    {
      "url": "https://example.com",
      "title": "Example Page",
      "content": "Page content...",
      "engine": "duckduckgo"
    }
  ],
  "query": "quantum computing"
}
```

### POST /scrape

Single-page scraping with privacy protection.

**Request:**
```json
{
  "url": "https://example.com",
  "query": "specific topic",
  "client_id": "default"
}
```

**Response:**
```json
{
  "success": true,
  "url": "https://example.com",
  "markdown": "Scraped content in markdown...",
  "title": "Page Title"
}
```

### POST /act

Execute browser actions (click, type, scroll).

**Request:**
```json
{
  "url": "https://example.com",
  "steps": [
    {"action": "click", "selector": "button.submit"},
    {"action": "type", "selector": "input[name=q]", "value": "search term"},
    {"action": "scroll", "value": "500"}
  ],
  "client_id": "default"
}
```

**Supported actions:** `click`, `type`, `scroll`, `click_xy`

### POST /workflow/execute

Alias for `/act`.

---

## Browser

### POST /login

Autonomous login using the Credential Vault.

**Request:**
```json
{
  "url": "https://example.com/login",
  "username": "user@example.com",
  "password": "secret",
  "client_id": "default"
}
```

**Response:**
```json
{
  "status": "success",
  "domain": "example.com",
  "message": "Login attempted for example.com"
}
```

---

## Privacy

### GET /privacy/report

Comprehensive privacy report.

**Response:**
```json
{
  "privacy": {
    "mode": "enhanced",
    "audit_statistics": {
      "total_entries": 0,
      "pii_detections": 0,
      "content_sanitizations": 0,
      "blocked_requests": 0,
      "credential_accesses": 0
    },
    "blocked_requests": [],
    "local_only": false,
    "pii_removal": true,
    "tracking_blocked": true
  },
  "audit": {
    "total_entries": 8,
    "by_category": {"browser": 6, "credential": 1, "error": 1},
    "oldest_entry": 1780924565.98,
    "newest_entry": 1780924569.14,
    "retention_days": 90
  },
  "vault_status": "locked"
}
```

### POST /privacy/mode

Set privacy mode for new sessions.

**Request:**
```json
{
  "mode": "maximum"
}
```

**Valid modes:** `standard`, `enhanced`, `maximum`, `local_only`

**Response:**
```json
{
  "success": true,
  "mode": "maximum",
  "message": "Privacy mode set to maximum"
}
```

### GET /privacy/check

Check if a URL is allowed under current privacy mode.

**Parameters:**
- `url` (string, required): URL to check

**Response:**
```json
{
  "url": "https://example.com",
  "allowed": true,
  "mode": "enhanced"
}
```

---

## Audit

### GET /audit/stats

Get audit statistics.

**Response:**
```json
{
  "total_entries": 8,
  "by_category": {"browser": 6, "credential": 1, "error": 1},
  "oldest_entry": 1780924565.98,
  "newest_entry": 1780924569.14,
  "retention_days": 90
}
```

### GET /audit/log

Get audit log entries.

**Parameters:**
- `category` (string, optional): Filter by category
- `limit` (int, optional): Max entries (default: 100)

**Response:**
```json
{
  "entries": [
    {
      "id": 1,
      "timestamp": 1780924565.98,
      "category": "browser",
      "action": "scrape",
      "details": {"url": "https://example.com"},
      "actor": "agent",
      "session_id": null,
      "hash": "abc123def456..."
    }
  ],
  "total": 8
}
```

### GET /audit/verify

Verify the integrity of the audit log chain.

**Response:**
```json
{
  "valid": true,
  "message": "Chain integrity verified"
}
```

---

## Vault

### GET /vault/status

Get vault lock status.

**Response:**
```json
{
  "locked": true,
  "access_log": []
}
```

### POST /vault/unlock

Unlock the credential vault.

**Request:**
```json
{
  "master_password": "your_password"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Vault unlocked"
}
```

### POST /vault/lock

Lock the credential vault.

**Response:**
```json
{
  "success": true,
  "message": "Vault locked"
}
```

### GET /vault/domains

List all domains with stored credentials.

**Response:**
```json
{
  "domains": ["example.com", "github.com"]
}
```

### GET /vault/credential

Find the best matching credential for a URL.

**Parameters:**
- `url` (string, required): URL to match

**Response:**
```json
{
  "found": true,
  "domain": "example.com",
  "username": "user@example.com"
}
```

---

## Security

### GET /security/verify

Verify supply chain integrity.

**Response:**
```json
{
  "timestamp": 1780924565.98,
  "packages": {
    "fastapi": {"version": "0.115.0", "verified": true, "hash": "abc123..."},
    "uvicorn": {"version": "0.30.0", "verified": true, "hash": "def456..."}
  },
  "system_components": {
    "python": true,
    "pip": true,
    "playwright": true,
    "sqlite_vec": true
  },
  "known_hashes_count": 10
}
```

### GET /security/verify/package

Verify a specific package's integrity.

**Parameters:**
- `package_name` (string, required): Package name

**Response:**
```json
{
  "name": "fastapi",
  "version": "0.115.0",
  "verified": true,
  "hash": "abc123def456..."
}
```

---

## Fingerprint

### POST /fingerprint/generate

Generate a new unique browser fingerprint.

**Request:**
```json
{
  "os_family": "macos"
}
```

**Response:**
```json
{
  "profile": {
    "profile_id": "fp_abc123",
    "user_agent": "Mozilla/5.0...",
    "viewport_width": 1920,
    "viewport_height": 1080,
    "timezone": "America/New_York",
    "language": "en-US"
  },
  "playwright_config": {
    "user_agent": "Mozilla/5.0...",
    "viewport": {"width": 1920, "height": 1080},
    "locale": "en-US",
    "timezone_id": "America/New_York"
  }
}
```

### GET /fingerprint/list

List all generated fingerprints.

**Response:**
```json
{
  "profiles": [
    {"profile_id": "fp_abc123", "user_agent": "Mozilla/5.0...", "created_at": 1780924565.98}
  ]
}
```

### GET /fingerprint/profile/{profile_id}

Get a specific fingerprint profile.

**Response:**
```json
{
  "profile": {...},
  "playwright_config": {...},
  "proxy_routing": {"proxy": null, "direct": true}
}
```

### POST /fingerprint/rotate

Generate a new fingerprint different from the current one.

**Parameters:**
- `current_profile_id` (string, optional): Current profile to differentiate from

---

## Knowledge Graph

### POST /knowledge/ingest

Ingest text into the knowledge graph.

**Request:**
```json
{
  "text": "OpenAI released GPT-4 in March 2023...",
  "url": "https://example.com/article"
}
```

**Response:**
```json
{
  "entities_found": 5,
  "relations_found": 3,
  "entities": [
    {"name": "OpenAI", "type": "org", "confidence": 0.95},
    {"name": "GPT-4", "type": "technology", "confidence": 0.98}
  ]
}
```

### GET /knowledge/graph

Get knowledge graph data for visualization.

**Parameters:**
- `max_nodes` (int, optional): Maximum nodes (default: 100)

### GET /knowledge/search

Search for entities in the knowledge graph.

**Parameters:**
- `query` (string, required): Search query
- `limit` (int, optional): Max results (default: 20)

### GET /knowledge/entity/{entity_id}

Get an entity and its relationships.

### GET /knowledge/clusters

Get topic clusters.

**Parameters:**
- `max_clusters` (int, optional): Max clusters (default: 10)

### GET /knowledge/stats

Get knowledge graph statistics.

---

## Missions

### POST /mission/schedule

Schedule a research mission.

**Request:**
```json
{
  "query": "Monitor AI safety news",
  "schedule": "0 */6 * * *",
  "priority": 1,
  "trigger_conditions": null,
  "client_id": "default"
}
```

### GET /mission/list

List all scheduled missions.

**Parameters:**
- `status` (string, optional): Filter by status

### POST /mission/start-scheduler

Start the background mission scheduler.

### POST /mission/stop-scheduler

Stop the background mission scheduler.

---

## Consensus

### POST /consensus/propose

Create a new consensus proposal.

**Request:**
```json
{
  "title": "Should we use Tor for research?",
  "description": "Proposal to enable Tor routing by default",
  "options": ["Yes", "No", "Abstain"],
  "required_nodes": 3
}
```

### GET /consensus/list

List all proposals.

**Parameters:**
- `status` (string, optional): Filter by status

### GET /consensus/proposal/{proposal_id}

Get proposal details.

### POST /consensus/vote

Cast a vote on a proposal.

**Request:**
```json
{
  "proposal_id": "prop_abc123",
  "node_id": "node_xyz",
  "choice": "Yes",
  "confidence": 0.9,
  "reasoning": "Tor provides better privacy"
}
```

### GET /consensus/tally/{proposal_id}

Tally votes for a proposal.

### GET /consensus/check/{proposal_id}

Check if consensus has been reached.

### POST /consensus/close/{proposal_id}

Close a proposal and record the result.

---

## Vision

### POST /vision/ocr

Extract text from a screenshot using LLM vision.

**Request:**
```json
{
  "image_data": "base64_encoded_png...",
  "language": "eng"
}
```

### POST /vision/ui-elements

Detect UI elements in a screenshot.

**Request:**
```json
{
  "image_data": "base64_encoded_png..."
}
```

### POST /vision/verify

Verify screen state matches expected description.

**Request:**
```json
{
  "image_data": "base64_encoded_png...",
  "expected": "Login form with username and password fields"
}
```

---

## Computer Use

macOS-only endpoints for screen capture and input control.

### GET /computer/capture

Capture screen region.

**Parameters:**
- `region` (string, optional): "full" or "frontmost" (default: "full")

**Response:**
```json
{
  "image_data": "base64_encoded_png...",
  "format": "png",
  "region": "full"
}
```

### POST /computer/mouse

Mouse control.

**Parameters:**
- `action` (string, required): "move", "click", "doubleclick", "rightclick", "drag"
- `x` (int, required): X coordinate
- `y` (int, required): Y coordinate
- `button` (string, optional): "left", "right", "middle"

### POST /computer/keyboard

Keyboard input.

**Parameters:**
- `text` (string, optional): Text to type
- `key` (string, optional): Special key name
- `modifiers` (list, optional): ["command", "shift", "option", "control"]

### POST /computer/launch

Launch a macOS application.

**Parameters:**
- `app_name` (string, required): Application name

### GET /computer/apps

List installed macOS applications.

---

## Multimodal

### POST /multimodal/image

Process an image (OCR, analysis, data extraction).

**Request:**
```json
{
  "image_data": "base64_encoded_image...",
  "filename": "screenshot.png",
  "task": "analyze"
}
```

### POST /multimodal/file

Process a file (CSV, JSON, markdown, code).

**Request:**
```json
{
  "file_data": "base64_encoded_file...",
  "filename": "data.csv"
}
```

### POST /multimodal/text

Process pasted text (URL detection, code recognition).

**Request:**
```json
{
  "text": "https://example.com/article about AI"
}
```

---

## WebSocket

### ws://localhost:8001/ws/{client_id}

Real-time agent state updates.

**Messages received:**
```json
{"type": "agent.state", "state": "thinking", "zone": "pile", "task_id": "abc123", "timestamp": 1780924565.98}
{"type": "agent.telemetry", "model": "gemma4:12b-it-qat", "action": "Planning research", "tokens_per_sec": 42.5, "timestamp": 1780924565.98}
{"type": "agent.reasoning", "delta": "Based on the research...", "task_id": "abc123", "timestamp": 1780924565.98}
{"type": "agent.task_start", "task_id": "abc123", "query": "What is...", "timestamp": 1780924565.98}
{"type": "agent.task_end", "task_id": "abc123", "status": "completed", "tokens_generated": 150, "elapsed_sec": 5.2, "timestamp": 1780924565.98}
```

**Agent states:** `idle`, `thinking`, `searching`, `reading`, `writing`, `error`

**Agent zones:** `pile` (search), `cabinet` (knowledge vault), `desk` (synthesis)

### ws://localhost:8001/ws/audit

Live audit log updates.

**Messages received:**
```json
{"type": "stats", "data": {"total_entries": 8, "by_category": {"browser": 6}}}
```

**Client can send:** `ping` → receives `pong`

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message",
  "path": "/endpoint/path"
}
```

**HTTP Status Codes:**
- `200` - Success
- `400` - Bad request (invalid parameters)
- `404` - Not found
- `422` - Validation error
- `500` - Internal server error
