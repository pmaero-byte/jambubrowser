# Local AI agents ↔ Jambubrowser (macOS)

Three ways for a local AI tool to drive every Jambubrowser use case.
Pick one — they all reach the same Python engine and the same 52 tools.

| Path | When to use | Needs app window? |
|---|---|---|
| **A. MCP stdio** (`bin/jambubrowser-mcp`) | Claude Desktop, Cursor, Cline, Windsurf, Zed — anything that spawns an MCP server | No |
| **B. Tauri `invoke`** (`agent_*` commands) | An agent embedded in (or scripting) the desktop app window | Yes |
| **C. HTTP** (engine REST + `/mcp/`) | Anything that speaks HTTP: scripts, Hammerspoon, Raycast, Shortcuts | No |

All three hit the same engine (default `http://127.0.0.1:8001`) the
macOS app's Rust sidecar boots. Use cases: **audit, browse, research,
memory, mesh, simulate, privacy, qa, missions, eval** — the catalog is
served live by `agent_bridge_status` (path B) and statically in
`docs/MCP_TOOLS.md`.

## A. MCP stdio (recommended)

1. Point your client at the launcher. Claude Desktop
   (`~/Library/Application Support/Claude/claude_desktop_config.json`):
   ```json
   { "mcpServers": { "jambubrowser": {
       "command": "/path/to/browser_project/bin/jambubrowser-mcp",
       "env": { "JAMBU_ENGINE_URL": "http://127.0.0.1:8001" } } } }
   ```
   A ready-made template is `examples/claude-desktop-config.json`.
   Cursor/Cline/Windsurf/Zed take the same `command` (+ optional `env`).
2. If the desktop app (or `bin/jambubrowser-engine`) already runs the
   engine, the launcher reuses it. Otherwise it boots a private engine
   on a free loopback port and tears it down on exit.
3. Profiles via `JAMBU_MCP_PROFILE`: `full` (52 tools) · `curated`
   (drops `execute_tool`) · `developer` (8 browser-testing verbs).

Verify without a client:
```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
 | ./bin/jambubrowser-mcp 2>/dev/null | python3 -c \
 "import json,sys; print(len([json.loads(l) for l in sys.stdin][2]['result']['tools']), 'tools')"
# → 52 tools
```

## B. In-app `invoke` (Tauri commands)

For agents that already hold the app window (WebView JS, sidecar
scripts via the Tauri CLI, test harnesses):

```js
import { invoke } from '@tauri-apps/api/core';
// One blob: engine health + tabs + MCP surface + use-case catalog
const status = await invoke('agent_bridge_status');
// Railed browsing (allowlist fail-closed, approvals, PII scrub)
const s = await invoke('agent_session_open', { allowDomains: ['example.com'], requireApproval: true });
await invoke('agent_session_act', { sessionId: s.session_id, action: 'navigate', url: 'https://example.com' });
await invoke('agent_session_close', { sessionId: s.session_id });
// One-shot jobs
await invoke('agent_run_audit', { url: 'https://staging.example.com', mode: 'quick' });
await invoke('agent_run_research', { query: 'solid-state batteries 2026', domain: 'academic' });
```

Commands live in `browser-app/src-tauri/src/commands/agent.rs`
(registered in `lib.rs` `invoke_handler`); the use-case table is
`AGENT_USE_CASES` in the same file. The capability blob embeds the MCP
surface via `GET /mcp/tools/docs?format=json`.

## C. Plain HTTP

- REST: `GET /health`, `POST /audit/run|/quick`, `POST /research`,
  `/browser/sessions*`, `/v2/agent/run`, `/v2/memory/*`, `/dcm/*`,
  `/vpn/*`, `/qa/*`, `/missions/*`, `/eval/*` (full list: `/openapi.json`).
- Remote MCP: `POST /mcp/` (Streamable HTTP, Bearer/`X-API-Key`),
  card at `/.well-known/mcp-server-card.json` — see `docs/MCP_REMOTE.md`.

## Engine lifecycle (macOS)

```bash
./bin/jambubrowser-engine               # foreground :8001 (Ctrl-C stops)
./bin/jambubrowser-engine --background  # detach, reuse if already up
./bin/jambubrowser-engine --stop        # stop it
```

The desktop app spawns the same engine on launch (Rust orchestrator
prefers `.venv/bin/python`). Opening the `.app` is enough; agents then
connect via A, B, or C with zero extra setup.

## macOS build (`.app` + `.dmg`)

```bash
./scripts/build.sh --skip-signing   # unsigned, runs locally after
                                    # right-click → Open (Gatekeeper)
```

Signed/notarized (releases): set `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
`APPLE_PASSWORD`, `APPLE_TEAM_ID` (see `scripts/build.sh`), or tag
`v*` — `.github/workflows/release.yml` builds aarch64 + x86_64,
signs, notarizes, and opens a draft release.

## Rails agents inherit (not optional)

Domain allowlist (fail-closed) · request-level network policy ·
approval gates for risky actions · PII scrubbing · hash-chained
receipts + evidence bundles · session TTL/step/element bounds.
Details: `docs/BROWSER_SESSIONS.md`. A refused action is an explicit
`403` (`blocked_domain`, `approval_required`, …), never a silent skip.
