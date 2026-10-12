# Yaadhum AI Desktop × JambuBrowser — local integration test

Date: 2026-10-13 · Machine: M-series macOS · Engine under test: this checkout.

## What was tested

The Yaadhum AI desktop app (`/Users/prabaharan/yaadhum_ncp/macos_app`, file
`JambuBrowserView.swift`) drives JambuBrowser through four HTTP calls, all
against `http://127.0.0.1:8001`:

| # | App code | Call |
| --- | --- | --- |
| 1 | `checkEngineHealth()` | `GET /openapi.json` (green/red dot) |
| 2 | `ensureSession()` | `POST /browser/sessions {"allow_domains": [host, "*"]}` |
| 3 | `navigate()` | `POST /browser/sessions/{id}/navigate {"url": …}` |
| 4 | `fetchSnapshot()` | `GET /browser/sessions/{id}/snapshot` → pane text |

The view then pipes `pageContent.prefix(800)` into the local Qwen 3.8 27B
(`engine.stream` → `:8085`) for its Deep Research / Siddha-citation buttons.

Harnesses: `/tmp/yaadhum_test/phase1_baseline.py` (live :8001) and
`/tmp/yaadhum_test/phase2_full.py` (source engine, :8011, 21 checks), plus the
repo harnesses `scripts/verify_greenwich_bridge.py` (54) and
`scripts/verify_greenwich_sse.py` (10).

## Results

**Bundled engine, as the app runs it today (`:8001`, release sidecar):**

| Call | Result |
| --- | --- |
| `GET /openapi.json` | 200 (dot shows green) |
| `GET /health`, `GET /browser/sessions`, `GET /audit/history` | 200 |
| `POST /browser/sessions` | **500** — the pane's very first real call |
| `GET /browser/privacy` | **500** |

Root cause, from the frozen binary's own traceback:

```
playwright._impl._errors.Error: Executable doesn't exist at
/private/var/folders/.../T/_MEI0000da863qD6RB/playwright/driver/package/
.local-browsers/chromium_headless_shell-1243/.../chrome-headless-shell
```

A PyInstaller onefile sidecar extracts its driver into a throwaway `_MEI…`
directory, so Playwright's default browser lookup lands inside that temp tree
— which no build step ever populated. The 3.4.0 bundle therefore shipped with
a browser pane that could never open a session, and its smoke test (`/health`
+ route count) could not see it.

**Source engine (`:8011`, same code + Greenwich bridge): 20/21 → 21/21 after
one assertion fix.** Highlights:

| Phase | Check | Result |
| --- | --- | --- |
| A | app's 4 calls (exact bodies) | session 0.8 s, navigate 0.15 s, snapshot 16 ms, `title='Example Domain'`, 917 chars |
| B | `file://` local asset from `yaadhum_ncp/openmodels/webapp/` | opens; AX tree 535 chars / 11 elements |
| C | type Tamil + Enter by AX ref | `SEARCH:அகத்தியர் ஞானவெட்டியான்` rendered in the page |
| C | click by AX ref (approved) | DOM changed (`PROBED`) |
| C | coordinate input, `GET /frame` | 21 KB JPEG at `/tmp/yaadhum_test/viewport.jpg` |
| C | `WS …/stream` | `mode=screencast`, 18 KB JPEG first frame |
| C | `GET …/events` (SSE) | `nav:start` → `nav:domcontentloaded` → `nav:networkidle` |
| D | `/v2/llm/chat` `provider=greenwich` | Qwen 3.8 27B described the page **from the AX tree** in 5.8 s |
| E | `/browser/sessions/{id}/sync-greenwich` | Memory Bridge `200`; bridge tokens 80 563 858 → 80 592 801 |

**Bridge endpoint drift (found, not a defect):** the deployed `:8095` now
implements the spec route `POST /v1/memory/ingest` (earlier it only had
`/v1/trajectory/record`). The dual-path ingest in
`backend/modules/greenwich_bridge.py` used it automatically — the harness now
reports which endpoint answered instead of asserting the fallback.

## Fixes applied

1. `browser-app/src-tauri/src/orchestrator/services.rs` — when spawning the
   frozen sidecar (and the checkout engine), set `PLAYWRIGHT_BROWSERS_PATH` to
   the shared Playwright cache unless the operator already set it.
   Verified: the same frozen binary that returned 500 returns **200** for
   `POST /browser/sessions` with the variable set, and completes
   navigate → snapshot (`title='Example Domain'`).
2. `scripts/build_engine_sidecar.sh` — ensures the headless shell is in the
   shared Playwright cache, documents why Chromium cannot be embedded (below),
   and the smoke test now boots the frozen engine **the way the app spawns it**
   (`PLAYWRIGHT_BROWSERS_PATH` set) and **creates a browser session**, failing
   the build if it cannot. Rebuilt bundle: `150M, 295 routes`, session create
   `200` in 1.9 s → `==> OK`.
3. Rust unit tests for the new resolver (`playwright_browsers_dir_from`):
   override wins, platform default cache, no-HOME case. `cargo test --lib
   orchestrator::services` → 8 passed.

### Why the browser is not embedded in the onefile (measured)

Embedding was tried first and fails inside PyInstaller, not in our code:

```
install_name_tool -id @rpath/libEGL.dylib .../chromium_headless_shell-1243/
  chrome-headless-shell-mac-arm64/libEGL.dylib
error: … larger updated load commands do not fit
  (the program must be relinked, and you may need to use -headerpad…)
```

Chromium ships `.dylibs` with unpadded Mach-O headers; PyInstaller rewrites
install names for every collected binary and the linker refuses. So the browser
stays in the shared cache and the app supplies the path. If a fully
self-contained `.app` is required later, the browser belongs next to the app
(Tauri `resources` → `Contents/Resources/ms-playwright`) rather than inside the
onefile.

## To give the running app a working engine now

- **Fastest, no rebuild:** quit Jambubrowser.app (it supervises :8001), then
  run the source engine on 8001:
  `ALLOW_LOCAL_WORKSPACES=true .venv/bin/python -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001`
- **Ship it properly:** `scripts/build_engine_sidecar.sh` (now bakes the
  browser and gated on a real session) then `npm run tauri build` in
  `browser-app/`.
- **No rebuild, side-by-side:** point `JambuBrowserView.swift`'s four URLs at
  `http://127.0.0.1:8011` and rebuild the Swift app.

The richer endpoints (AX tree, act-by-ref, frame/stream, events,
sync-greenwich) are what turn that pane from a text read-out into an
interactive, memory-writing browser the local model can actually drive.
