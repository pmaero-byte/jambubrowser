# Greenwich Studio × JambuBrowser Bridge

Target: macOS Apple Silicon (M-series UMA) — Greenwich Studio (SwiftUI) +
Safe Qwen 3.8 27B (`:8085`) + Quantum Daemon (`:8090`) + Memory Bridge
(`:8095`) + JambuBrowser Engine v3.4.0 (`:8001`).

## Verification status (live, against the running stack)

`64/64` live checks pass across two harnesses (both runnable from this repo):

```bash
.venv/bin/python scripts/verify_greenwich_bridge.py   # §1,2,3,4,5,7 (real browser)
.venv/bin/python scripts/verify_greenwich_sse.py      # §6 (real HTTP SSE)
```

Verified against the deployed daemons on this machine: `:8085` answered a real
Qwen 3.8 27B completion ("pong", 0.86 s), `:8090` produced a real daemon
completion (≈7.6 s / 8 tokens), `:8095` accepted a real ingest and reported
80.5 M tokens ingested across 2 242 indexed files.

### Deployed API reality vs. the spec

| Spec assumption | What the running stack exposes | Bridge behaviour |
| --- | --- | --- |
| `POST :8095/v1/memory/ingest` | **No such route.** The bridge exposes `/v1/project/memory` (status), `/v1/project/memory/sync` (workspace re-index) and `/v1/trajectory/record` (TITO record with `input_text`/`output_text`/`metadata`) | `ingest_memory()` tries the spec path first, then falls back to `/v1/trajectory/record`; the response reports which endpoint accepted it |
| `:8085/v1` OpenAI chat | llama-server: OpenAI-compatible, fast | works as spec'd |
| `:8090/v1` OpenAI chat | FastAPI daemon with `/v1/chat/completions` + `/generate`, `/chat/stream`, `/health`; **no `/v1/models`** | provider health treats 404 on `/v1/models` as "server up"; fallback verified against the live daemon |
| `sync_to_greenwich_memory` flag | not a deployed concept | implemented engine-side on research / scrape / audit / session-open + `POST /browser/sessions/{id}/sync-greenwich` |

## Bugs found and fixed by the verification pass

1. **Live-viewport input would have silently emulated.** `send_input` /
   `screencast_jpeg` existed on the low-level `BrowserSession`, not on the
   `PlaywrightPage` adapter the agent actually drives — so `/input` fell through
   to a "click the first element" stub and `/frame` produced PNGs. Both are now
   real adapter methods (mouse/keyboard + CDP `Page.captureScreenshot`).
2. **`file://` was aborted with `ERR_BLOCKED_BY_CLIENT`.** The per-request
   network policy only allowed http/https/ws/wss, so the route killed local
   previews before they loaded. `NetworkPolicy.decide` now admits `file:` URLs
   confined to the workspace roots.
3. **Local pages were reverted after loading.** The post-navigation allowlist
   check used `host_allowed()`, and a `file://` URL has no host — every local
   preview bounced to `about:blank`. Added `_url_allowed_after_nav()`, which
   judges `file:` by workspace confinement.
4. **`nav:start` was never emitted** by browser sessions (only by audits), and
   `press_enter` called a non-existent adapter method. Navigation now emits
   `nav:start` → `nav:domcontentloaded` (+title) → `nav:networkidle`, refusals
   emit `error`, and `press_enter` uses the adapter's ref-scoped `press`.
5. **`session_frame` lost its body** during development (the `/frame` route
   returned `null`). Repaired, and a suite-wide AST audit now confirms every
   route handler has a return.

## 1. Shared Chromium + process reaper (Improvement 1)


- `backend/modules/browser.py`: `get_shared_browser()` /
  `close_shared_browser()`; ephemeral + local-only sessions multiplex
  `BrowserContext`s on one Chromium. Persistent/Tor keep dedicated launches.
- Headless agent profile: `--headless=new --disable-gpu
  --disable-gpu-compositing --disable-software-rasterizer` unless
  `JAMBU_BROWSER_GPU=on`. Extra flags in `_chromium_headless_args()`.
- Idle TTL: `JAMBU_BROWSER_IDLE_TTL` / `JAMBU_SESSION_IDLE_TTL` (default 300s).
  `BrowserManager.reap_idle()` + `BrowserAgentService._prune()` evict on
  `last_activity`; `touch()` is called on navigate/act/snapshot/input.
- Shutdown: `close_all()` closes contexts then the shared browser;
  `engine.py` already calls `cleanup_browser()` on shutdown.

## 2. Live viewport (Improvement 2)

- `WS /browser/sessions/{id}/stream` — base64 JPEG frames + JSON input
  (`click|move|scroll|keydown|type|press`). Frames come from a pushed CDP
  `Page.startScreencast(format=jpeg, quality=70, everyNthFrame=1)` when the
  adapter has CDP (`"mode": "screencast"`), with a 2 s keep-alive poll because
  Chromium only pushes on visual damage; a non-CDP / fake page falls back to
  pure polling (`"mode": "poll"`). Frames are acked automatically — Chromium
  stops the stream otherwise.
- `POST /browser/sessions/{id}/input` — `{type,x,y,delta_x,delta_y,key,text}`.
- `GET /browser/sessions/{id}/frame?quality=80` — single CDP JPEG (polling /
  poster-frame fallback).

Delivery is verified live: first WS message is a frame, JPEG magic bytes
checked, and client-sent clicks/keys are applied (see harness §2 checks).

## 3. AX tree + act-by-ref (Improvement 3)

- `GET /browser/sessions/{id}/ax-tree` — pruned `[@N: role "name" href=…]`
  with implicit ARIA roles (an `<a>` prints as `link`, an `<input>` as
  `textbox`/`searchbox`), `value=` for fields, `(disabled)`, `(focused)` from
  the live DOM focus, and `[frame:n]` for iframe-owned elements.
- `POST /browser/sessions/{id}/act-by-ref` —
  `{action, ref:int, text, approve, press_enter}`.
- **Rails still apply**: an element the risk classifier calls irreversible
  ("Submit", "Delete", "Pay"…) returns `403 approval_required` even when the
  session was opened with `require_approval: false`. Re-send with
  `approve: true` — that is the prompt-injection guard, not a bug.


## 4. Greenwich LLM provider (Improvement 4)

- `backend/llm/providers/greenwich.py` — OpenAI-compatible `:8085` (+`:8090`
  fallback), SSE streaming, `chat_template_kwargs.enable_thinking` passthrough,
  `<think>` stripped unless `GREENWICH_ENABLE_THINKING=1`.
- Env: `GREENWICH_BASE_URL`, `GREENWICH_EXTENDED_URL`, `GREENWICH_MODEL`,
  `GREENWICH_API_KEY`. Chain: `JAMBU_LLM_FALLBACK_CHAIN=greenwich,…`.
- Local + zero-cost: added to `LOCAL_PROVIDERS` (routing + pricing).

## 5. Memory Bridge sync (Improvement 5)

- `backend/modules/greenwich_bridge.py` owns the contract: `ingest_memory()`
  POSTs the spec body to `/v1/memory/ingest` and, when that 404s (the deployed
  bridge has no such route), re-posts a TITO body to
  `/v1/trajectory/record` (`input_text`/`output_text`/`role`/`metadata`).
  The response reports `{"ok", "endpoint", "status"}`.
- Base URL: `GREENWICH_MEMORY_URL` (default `http://127.0.0.1:8095`);
  `memory_status()` reads `GET /v1/project/memory` for UI liveness checks.
- Flags: `sync_to_greenwich_memory` on research, scrape, audit + session open;
  `POST /browser/sessions/{id}/sync-greenwich` for manual sync.
- Audit score: `100 − 4·critical − 2·high − 0.5·medium`.
- Verified live against the deployed bridge: route `200`, endpoint reported as
  `/v1/trajectory/record`.

## 6. SSE event bus (Improvement 6)

- `GET /browser/sessions/{id}/events` — `nav:start`, `nav:domcontentloaded`,
  `nav:networkidle`, `audit:step`, `error`, heartbeats.
- `manager.session_subscribe/emit_session_event/session_unsubscribe`;
  audit fans out when `session_id` is set on the audit request.

## 7. Local workspaces (Improvement 7)

- `ALLOW_LOCAL_WORKSPACES=true` (or `JAMBU_ALLOW_LOCAL_WORKSPACES`) lifts
  loopback (`localhost`/`127.0.0.1`) + `file://`; rest of RFC1918 stays blocked.
- `file://` confined to `JAMBU_LOCAL_WORKSPACE_ROOTS` (default `~`).
- Agent gates (`_check_navigation`, `host_allowed`) honour the same toggle.
