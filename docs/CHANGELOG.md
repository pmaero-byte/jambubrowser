# Changelog

All notable changes to Jambubrowser.

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
