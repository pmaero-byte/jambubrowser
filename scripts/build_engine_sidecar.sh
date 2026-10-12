#!/usr/bin/env bash
# Build the Python engine as a self-contained sidecar binary for the desktop
# app. Produces `browser-app/src-tauri/binaries/jambubrowser-engine-<triple>`
# which Tauri's `externalBin` copies into `Jambubrowser.app/Contents/MacOS/`,
# letting the bundled app boot its own engine with no system Python, no venv
# and no checkout on the target machine.
#
# Usage:  scripts/build_engine_sidecar.sh          # then `npm run tauri build`
# Note:   run this BEFORE `tauri build` — bundling fails if externalBin is
#         missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "missing $PY — create the venv first" >&2; exit 1; }

ARCH="$(uname -m)"
case "$ARCH" in
  arm64) TRIPLE="aarch64-apple-darwin" ;;
  x86_64) TRIPLE="x86_64-apple-darwin" ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 1 ;;
esac
OUT_DIR="$ROOT/browser-app/src-tauri/binaries"
NAME="jambubrowser-engine"

echo "==> installing pyinstaller (pinned by pip resolver)"
"$PY" -m pip install --quiet pyinstaller

# Why the browser is NOT baked inside the onefile: Chromium ships .dylibs
# with unpadded Mach-O headers, and PyInstaller's binary collector rewrites
# their install names during packaging, which the linker rejects
# ("install_name_tool … larger updated load commands do not fit"). The frozen
# app therefore keeps its browser in the shared Playwright cache and the
# *desktop app* points the engine at it (PLAYWRIGHT_BROWSERS_PATH in
# browser-app/src-tauri/src/orchestrator/services.rs). Make sure this build
# machine has the shell so the smoke test below exercises the real path.
echo "==> ensuring chromium headless shell is in the shared Playwright cache"
"$PY" -m playwright install chromium-headless-shell
BROWSERS_DIR="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/Library/Caches/ms-playwright}"
ls -d "$BROWSERS_DIR"/chromium_headless_shell-* >/dev/null 2>&1 \
  || echo "    WARNING: no chromium_headless_shell-* in $BROWSERS_DIR — target machines will need 'playwright install chromium-headless-shell'"
# A stray in-package browser from an earlier experiment must not be embedded:
# it is exactly what makes PyInstaller fail, and it is unused at runtime.
PLAYWRIGHT_PKG="$("$PY" -c 'import playwright, os; print(os.path.dirname(playwright.__file__))')"
rm -rf "$PLAYWRIGHT_PKG/driver/package/.local-browsers"

echo "==> freezing engine (onefile) for $TRIPLE"
"$PY" -m PyInstaller \
  --noconfirm --clean --onefile \
  --name "$NAME" \
  --paths "$ROOT" \
  --collect-submodules backend \
  --collect-all sqlite_vec \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.loops.asyncio \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.http.h11_impl \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --exclude-module torch \
  --exclude-module mlx \
  --exclude-module mlx_lm \
  --distpath "$ROOT/.build/sidecar" \
  bin/engine_sidecar.py

mkdir -p "$OUT_DIR"
cp ".build/sidecar/$NAME" "$OUT_DIR/$NAME-$TRIPLE"
cp ".build/sidecar/$NAME" "$OUT_DIR/$NAME"
chmod +x "$OUT_DIR/$NAME-$TRIPLE" "$OUT_DIR/$NAME"

echo "==> smoke test: boot frozen engine on :8299 (as the Tauri app spawns it)"
# Mirror the app's runtime environment exactly — including the browser path the
# desktop app supplies — so this test fails if the app would fail.
JAMBU_DB_PATH=/tmp/sidecar_smoke.db \
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/Library/Caches/ms-playwright}" \
  "$OUT_DIR/$NAME" --port 8299 &
SMOKE_PID=$!
trap 'kill "$SMOKE_PID" 2>/dev/null || true; rm -f /tmp/sidecar_smoke.db' EXIT
for _ in $(seq 1 30); do
  curl -sf --max-time 2 http://127.0.0.1:8299/health >/dev/null 2>&1 && break
  sleep 1
done
curl -sf --max-time 5 http://127.0.0.1:8299/health >/dev/null \
  || { echo "sidecar failed /health smoke test" >&2; exit 1; }
ROUTES="$(curl -sf --max-time 5 http://127.0.0.1:8299/openapi.json | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["paths"]))')"
[ "$ROUTES" -gt 100 ] || { echo "sidecar serves only $ROUTES routes — bundle is incomplete" >&2; exit 1; }

# The app's very first real call is `POST /browser/sessions`, and that is the
# one a browser-less bundle fails. /health alone never caught it: the 3.4.0
# release shipped with a green smoke test and a dead browser pane.
echo "==> smoke test: create a browser session (the app's first real call)"
CODE="$(curl -s -o /tmp/sidecar_session.json -w '%{http_code}' --max-time 90 \
  -X POST http://127.0.0.1:8299/browser/sessions \
  -H 'content-type: application/json' -d '{"allow_domains":["example.com"]}')"
[ "$CODE" = "200" ] || {
  echo "sidecar cannot create a browser session (HTTP $CODE): $(head -c 300 /tmp/sidecar_session.json)" >&2
  echo "hint: is the headless shell inside playwright/driver/package/.local-browsers?" >&2
  exit 1
}
SESSION_ID="$(python3 -c 'import json;print(json.load(open("/tmp/sidecar_session.json"))["session_id"])')"
curl -sf --max-time 15 -X DELETE "http://127.0.0.1:8299/browser/sessions/$SESSION_ID" >/dev/null 2>&1 || true
rm -f /tmp/sidecar_session.json

echo "==> OK: $OUT_DIR/$NAME-$TRIPLE ($(du -h "$OUT_DIR/$NAME-$TRIPLE" | cut -f1), $ROUTES routes)"
