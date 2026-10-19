#!/usr/bin/env bash
#
# One command, on a clean machine, from a fresh checkout:
#
#     bash scripts/verify_install.sh
#
# Creates a virtualenv, installs the declared dependencies, installs the
# headless browser, boots the engine, runs scripts/verify_install.py against it,
# and shuts the engine down again. Everything it needs is declared in
# requirements.txt — if this script fails, the install is genuinely broken,
# which is the point.
#
# Environment:
#   VENV_DIR      virtualenv location        (default: .venv)
#   PORT          engine port                (default: 8001)
#   SKIP_INSTALL  reuse an existing venv     (default: 0)
#   SKIP_NETWORK  skip live-site checks      (default: 0)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="${VENV_DIR:-.venv}"
PORT="${PORT:-8001}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"
SKIP_NETWORK="${SKIP_NETWORK:-0}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/jambu-verify-engine.log}"
PY="${VENV_DIR}/bin/python"

step() { printf '\n==> %s\n' "$1"; }
fail() { printf '\n!! %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Interpreter
# ---------------------------------------------------------------------------

step "Checking the interpreter"
command -v python3 >/dev/null || fail "python3 not found"
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 - <<'PY' || fail "Python 3.9+ is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
echo "python3 ${PY_VER}"

# ---------------------------------------------------------------------------
# 2. Virtualenv + dependencies
# ---------------------------------------------------------------------------

if [ "${SKIP_INSTALL}" = "1" ]; then
  step "Reusing ${VENV_DIR} (SKIP_INSTALL=1)"
  [ -x "$PY" ] || fail "SKIP_INSTALL=1 but ${PY} does not exist"
else
  step "Creating ${VENV_DIR}"
  [ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
  "$PY" -m pip install --quiet --upgrade pip

  step "Installing requirements.txt (this is the real test of the install)"
  "$PY" -m pip install --quiet -r requirements.txt \
    || fail "pip install -r requirements.txt failed — the install is broken"

  step "Installing the headless browser (Playwright Chromium)"
  "$PY" -m playwright install chromium \
    || fail "playwright install chromium failed — browser rendering cannot work"
fi

# ---------------------------------------------------------------------------
# 3. Boot the engine
# ---------------------------------------------------------------------------

step "Starting the engine on port ${PORT}"
"$PY" -m uvicorn backend.engine:app --host 127.0.0.1 --port "$PORT" \
  >"$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!

cleanup() {
  if kill -0 "$ENGINE_PID" 2>/dev/null; then
    kill "$ENGINE_PID" 2>/dev/null || true
    wait "$ENGINE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo "--- engine log ---"; tail -40 "$ENGINE_LOG"
    fail "the engine exited during startup"
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 \
  || { echo "--- engine log ---"; tail -40 "$ENGINE_LOG"; fail "the engine never became healthy"; }

# ---------------------------------------------------------------------------
# 4. Verify
# ---------------------------------------------------------------------------

step "Running end-to-end verification"
VERIFY_ARGS=(--base-url "http://127.0.0.1:${PORT}")
[ "${SKIP_NETWORK}" = "1" ] && VERIFY_ARGS+=(--skip-network)

set +e
"$PY" scripts/verify_install.py "${VERIFY_ARGS[@]}"
VERIFY_RC=$?
set -e

if [ "$VERIFY_RC" -ne 0 ]; then
  echo
  echo "Engine log: ${ENGINE_LOG}"
fi
exit "$VERIFY_RC"
