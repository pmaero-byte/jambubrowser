# Jambubrowser API Documentation

## Overview

Jambubrowser is a sovereign autonomous research engine with privacy-first architecture. All data stays on your machine by default.

**Base URL:** `http://localhost:8001`

---

## System Endpoints

### GET /health
Check system health and component status.

**Response:**
```json
{
  "status": "healthy",
  "version": "2.0.0",
  "uptime_seconds": 12345,
  "components": {
    "database": "ok",
    "memory": "ok",
    "browser": "ok"
  }
}
```

### GET /stats
Get system statistics.

**Response:**
```json
{
  "total_tasks": 100,
  "active_sessions": 5,
  "memory_nodes": 1234,
  "vault_status": "locked"
}
```

---

## Research Endpoints

### POST /research
Autonomous swarm research with multi-engine search.

**Request:**
```json
{
  "query": "What is quantum computing?",
  "client_id": "ui",
  "brain_only": false,
  "domain": "general"
}
```

**Response:**
```json
{
  "answer": "Quantum computing uses...",
  "sources": ["https://example.com"],
  "doc_count": 10,
  "context": "Additional context..."
}
```

### GET /search
Raw metasearch with multiple engines.

**Parameters:**
- `q` (string, required): Search query
- `max_results` (int, optional): Maximum results (default: 10)

**Response:**
```json
{
  "results": [
    {
      "title": "Example",
      "url": "https://example.com",
      "snippet": "Description..."
    }
  ]
}
```

---

## Browser Endpoints

### POST /scrape
Scrape a single page with privacy protection.

**Request:**
```json
{
  "url": "https://example.com",
  "wait_until": "networkidle",
  "mode": "ephemeral",
  "privacy_level": "enhanced"
}
```

**Response:**
```json
{
  "success": true,
  "url": "https://example.com",
  "title": "Example Domain",
  "text_content": "Page content...",
  "screenshot_base64": "...",
  "privacy_report": {...}
}
```

### POST /act
Execute browser actions (click, type, scroll).

**Request:**
```json
{
  "action": "click",
  "selector": "#button",
  "url": "https://example.com"
}
```

**Actions:** `click`, `type`, `scroll`, `click_xy`

### POST /workflow/execute
Execute a workflow with multiple steps.

**Request:**
```json
{
  "steps": [
    {"action": "navigate", "url": "https://example.com"},
    {"action": "click", "selector": "#link"},
    {"action": "extract", "selector": ".content"}
  ]
}
```

---

## Memory Endpoints

### GET /memory/recall
Recall information from memory.

**Parameters:**
- `query` (string, required): Search query
- `limit` (int, optional): Maximum results (default: 5)

**Response:**
```json
{
  "memories": [
    {
      "id": 1,
      "content": "Memory content...",
      "relevance": 0.95
    }
  ]
}
```

### POST /knowledge/ingest
Ingest content into knowledge graph.

**Request:**
```json
{
  "text": "Content to ingest...",
  "url": "https://example.com",
  "metadata": {}
}
```

**Response:**
```json
{
  "entities_extracted": 5,
  "relations_extracted": 3,
  "total_entities": 100
}
```

### GET /knowledge/graph
Get knowledge graph data.

**Response:**
```json
{
  "nodes": [...],
  "edges": [...]
}
```

---

## Credential Vault Endpoints

### POST /login
Store credentials securely.

**Request:**
```json
{
  "url": "https://github.com/login",
  "username": "user@example.com",
  "password": "secure_password"
}
```

### GET /vault/credential
Retrieve credentials.

**Parameters:**
- `url` (string, required): URL to retrieve credentials for

### GET /vault/domains
List all stored domains.

---

## Privacy Endpoints

### GET /privacy/report
Get privacy status report.

**Response:**
```json
{
  "mode": "enhanced",
  "network": {
    "local_only": false,
    "external_requests_allowed": true,
    "blocked_domains_count": 1000
  },
  "content": {
    "pii_detection_enabled": true,
    "tracking_protection": true
  },
  "audit": {
    "enabled": true,
    "chain_valid": true,
    "total_entries": 500
  },
  "vault": {
    "locked": true,
    "credentials_count": 10
  }
}
```

### POST /privacy/check
Check content for PII.

**Request:**
```json
{
  "content": "John Doe's email is john@example.com"
}
```

**Response:**
```json
{
  "has_pii": true,
  "detected_types": ["email", "name"],
  "sanitized_content": "[REDACTED]"
}
```

---

## Audit Endpoints

### GET /audit/log
Get audit log entries.

**Parameters:**
- `category` (string, optional): Filter by category
- `limit` (int, optional): Maximum entries (default: 100)

**Categories:** `research`, `browser`, `credential`, `network`, `privacy`, `system`, `error`

**Response:**
```json
{
  "entries": [
    {
      "id": 1,
      "timestamp": 1234567890,
      "category": "browser",
      "action": "scrape",
      "details": {"url": "https://example.com"},
      "hash": "abc123..."
    }
  ]
}
```

### GET /audit/stats
Get audit statistics.

**Response:**
```json
{
  "total_entries": 500,
  "categories": {"browser": 100, "research": 200},
  "chain_valid": true,
  "oldest_entry": 1234567890,
  "newest_entry": 1234567899
}
```

### GET /audit/verify
Verify audit log chain integrity.

**Response:**
```json
{
  "valid": true,
  "message": "Chain integrity verified"
}
```

---

## Security Endpoints

### GET /security/verify
Verify supply chain integrity.

**Response:**
```json
{
  "verified": true,
  "dependencies": {
    "fastapi": {"status": "ok", "version": "0.104.0"},
    "playwright": {"status": "ok", "version": "1.40.0"}
  }
}
```

---

## Browser Session Endpoints

### POST /fingerprint/generate
Generate a new browser fingerprint.

**Response:**
```json
{
  "profile_id": "fp_abc123",
  "user_agent": "Mozilla/5.0...",
  "viewport": {"width": 1920, "height": 1080}
}
```

### POST /fingerprint/rotate
Rotate to a new fingerprint.

**Response:**
```json
{
  "new_profile_id": "fp_def456",
  "rotated": true
}
```

### GET /fingerprint/list
List all generated fingerprints.

### POST /browser/privacy
Set privacy level for browser sessions.

**Request:**
```json
{
  "level": "maximum",
  "session_id": "optional_session_id"
}
```

---

## WebSocket Endpoints

### WS /ws/{client_id}
Real-time agent logging.

**Messages:**
```json
{"type": "log", "message": "Task started"}
{"type": "progress", "value": 0.5}
{"type": "complete", "result": {...}}
```

### WS /ws/audit
Live audit log updates.

**Messages:**
```json
{"type": "stats", "data": {...}}
{"type": "entry", "entry": {...}}
```

---

## Mission Endpoints

### POST /mission
Create a new mission.

**Request:**
```json
{
  "name": "Daily Research",
  "task": "Research AI news",
  "schedule": "0 9 * * *"
}
```

### GET /mission/list
List all missions.

### POST /mission/schedule
Schedule a mission.

### POST /mission/start-scheduler
Start the mission scheduler.

### POST /mission/stop-scheduler
Stop the mission scheduler.

---

## Tool Endpoints

### POST /tool/save
Save a custom tool.

**Request:**
```json
{
  "name": "web_scraper",
  "code": "def scrape(url): ...",
  "description": "Custom web scraper"
}
```

### GET /tools
List all saved tools.

### POST /tool/exec
Execute a saved tool.

**Request:**
```json
{
  "tool_name": "web_scraper",
  "args": {"url": "https://example.com"}
}
```

---

## Consensus Endpoints

### POST /consensus/propose
Create a consensus proposal.

**Request:**
```json
{
  "proposal": "Should we enable maximum privacy?",
  "options": ["yes", "no", "abstain"]
}
```

---

## Error Responses

All endpoints return standard error responses:

```json
{
  "detail": "Error message"
}
```

**Status Codes:**
- `200`: Success
- `400`: Bad request
- `404`: Not found
- `500`: Internal server error

---

## Privacy Levels

| Level | Features |
|-------|----------|
| `standard` | Basic fingerprinting protection |
| `enhanced` | Fingerprint rotation + cookie blocking |
| `maximum` | Tor + no JS + no persistence + sanitization |

---

## Session Modes

| Mode | Description |
|------|-------------|
| `persistent` | Full state persistence (cookies, localStorage) |
| `ephemeral` | In-memory only, destroyed on close |
| `tor_isolated` | Tor-routed with stream isolation |
| `local_only` | No external network calls allowed |
