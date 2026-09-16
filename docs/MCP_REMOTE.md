# Remote MCP — Streamable HTTP with token auth

`backend/mcp_server.py` is the stdio server for local installs (Claude
Desktop, Cursor). `backend/mcp_http.py` exposes the same **28 tools** over
**Streamable HTTP** — the transport 55% of registry servers now use and
the one the 2026 MCP roadmap builds on (SSE is deprecated).

## What a remote deployment serves

| Path | Auth | Purpose |
|---|---|---|
| `POST /mcp/` | **required** | MCP Streamable HTTP endpoint |
| `GET /health` | public | liveness |
| `GET /.well-known/mcp-server-card.json` | public | Server Card (discovery) |

Auth accepts either:

- `Authorization: Bearer <key>` — an engine API key
  (`POST /api-keys/create`) or the static `JAMBU_MCP_TOKEN`; or
- `X-API-Key: <key>` (same credentials).

Unauthorized requests get `401` with a `WWW-Authenticate: Bearer` challenge
and a message that says exactly which credential to send. The registry
census found thousands of unauthenticated remote MCP servers; this one
cannot be deployed that way. No CORS headers are emitted — this is a
server-to-server surface, not a browser one.

## Run it

Standalone (recommended behind a TLS reverse proxy):

```bash
export JAMBU_MCP_TOKEN="$(openssl rand -hex 24)"
export JAMBU_MCP_PUBLIC_URL="https://mcp.example.com"   # used by the Server Card
python -m uvicorn backend.mcp_http:app --host 127.0.0.1 --port 8765
curl -s localhost:8765/.well-known/mcp-server-card.json | jq .tools
```

Mounted in the engine (single port, existing middleware stack):

```bash
JAMBU_MCP_TOKEN=... python -m uvicorn backend.engine:app --port 8001
# MCP endpoint: http://127.0.0.1:8001/mcp/
```

Client configuration (Claude, Cursor, any MCP client with remote support):

```json
{
  "mcpServers": {
    "jambubrowser": {
      "type": "streamable-http",
      "url": "https://mcp.example.com/mcp/",
      "headers": { "Authorization": "Bearer <engine-api-key>" }
    }
  }
}
```

## Tool profiles

`JAMBU_MCP_PROFILE` (default `full`):

- `full` — all 28 tools.
- `curated` — 27 tools; drops `execute_tool` (arbitrary saved-tool
  execution) so remote callers must opt in explicitly to that capability.
  Curated also keeps the surface compact for better tool selection.

## Discovery / registry

`server.json` is a registry manifest (schema-validated shape) for
publishing to the official MCP Registry. Publishing is **deliberately not
done yet**: it requires a deployed HTTPS endpoint and verification of the
namespace (`io.github.pmaero-byte` via GitHub OAuth, or a domain namespace
via DNS). Until then the card is the discovery surface — and the card is
honest: it lists auth, transports, the active profile, and the tool names.

## Operational notes

- The MCP SDK's `StreamableHTTPSessionManager` may be run only once per
  FastMCP instance, so `backend.mcp_http.reset_session_manager()` exists
  for tests and in-process restarts. The engine lifespan runs the manager;
  Starlette does not run lifespans of mounted sub-apps itself.
- Requests are stateless (`stateless_http=True`), so horizontal scaling
  needs no session store.
- `/mcp` is exempt from the engine's 30s request timeout (tool calls can
  legitimately run for minutes: audits, mesh inference).
- Latency budget: tool calls proxy to the same engine that hosts them, so
  a remote deployment adds one network hop, nothing else.
