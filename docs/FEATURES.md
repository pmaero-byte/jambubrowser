# Feature Map: Jambubrowser Evolution

This document maps the major feature areas of Jambubrowser, a privacy-first
autonomous research browser. For a file-level, pillar-by-pillar breakdown of
what actually ships, see `FEATURE_MAP.md`.

## 🧠 Intelligence & Reasoning
- **Autonomous Swarm Research**: Multi-agent orchestration (planner, critic, executor, verifier) decomposes complex queries into sub-tasks.
- **Federated RAG**: Queries trusted peers for answers over the P2P research mesh.
- **Consensus Voting**: Multi-node proposal/vote/tally for federated decisions.
- **Agent Intuition**: Pre-research hypothesis generation (`generate_hypothesis`).
- **Cross-Session Memory**: Semantic recall of research from previous sessions via the 4-store memory system.

## 🌐 Exploration & Sovereignty
- **Hybrid Workspace**: Persistent agent sidebar alongside a standard browser view.
- **Source Proxy**: Server-side proxy strips `X-Frame-Options` so original sources render inside the browser pane.
- **Deep Web Routing**: Optional SOCKS5 client wrapper (`backend/core/socks.py`) that routes traffic through a user-supplied Tor proxy via `JAMBU_TOR_SOCKS_URL`. No integrated Tor daemon — you bring your own proxy.
- **Forensic Safety**: Isolated browser sessions with four privacy modes (Standard / Enhanced / Maximum / Local-Only).
- **Autonomous Risk Assessment**: URL risk pre-screening before visiting (`risk_shield`).
- **Credential Vault**: Local-only AES-256-GCM encrypted credential storage with auto form-fill.

## ⚡ Action & Precision (Computer Use)
- **Visual Grounding**: The agent "sees" the page and identifies buttons/forms.
- **Coordinate Clicking**: High-precision interaction bypassing fragile CSS selectors.
- **Self-Improving Toolbox**: On failure, the agent has an LLM write a new tool, sandbox-tests it, and persists it (skill synthesizer).
- **Temporal Scheduling**: Cron-based mission monitor for background research.

## 🎨 Professional Interface
- **v4 AppShell Interface**: Sidebar-driven layout with lazy-loaded panels, built on React 19 + Tailwind in a Tauri 2 shell.
- **Command Palette**: ⌘K navigation and actions (cmdk).
- **2D Knowledge Graph**: Interactive 2D force-graph mapping of your local knowledge vault (`react-force-graph-2d`).
- **Live Telemetry**: Real-time tracking of tokens/sec, CPU, and RAM via WebSocket and system endpoints.
- **iOS Companion**: SwiftUI companion app (`ios-app/`) with gateway and keychain services.
