#!/usr/bin/env bash
#
# Jambubrowser local dev mode
# ---------------------------
# Starts the backend, the MLX/Ollama LLM (if available), and the Tauri/React dev server.
# All three in one command, with colored logs.
#
# Usage:  ./scripts/dev.sh [--no-llm] [--no-backend]
#

set -euo pipefail

# Resolve project root regardless of where this is invoked from
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------- Colors ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---------- Flags ----------
NO_LLM=0
NO_BACKEND=0
for arg in "$@"; do
  case "$arg" in
    --no-llm) NO_LLM=1 ;;
    --no-backend) NO_BACKEND=1 ;;
    -h|--help)
      echo "Usage: $0 [--no-llm] [--no-backend]"
      exit 0
      ;;
  esac
done

cleanup() {
  echo
  echo -e "${YELLOW}Shutting down...${NC}"
  if [[ -n "${BACKEND_PID:-}" ]]; then kill "$BACKEND_PID" 2>/dev/null || true; fi
  if [[ -n "${LLM_PID:-}" ]]; then kill "$LLM_PID" 2>/dev/null || true; fi
  if [[ -n "${TAURI_PID:-}" ]]; then kill "$TAURI_PID" 2>/dev/null || true; fi
  exit 0
}
trap cleanup INT TERM EXIT

echo -e "${BOLD}${CYAN}╭───────────────────────────────────────╮${NC}"
echo -e "${BOLD}${CYAN}│   Jambubrowser local dev launcher     │${NC}"
echo -e "${BOLD}${CYAN}╰───────────────────────────────────────╯${NC}"
echo

# ---------- Backend ----------
if [[ $NO_BACKEND -eq 0 ]]; then
  echo -e "${BLUE}[1/3]${NC} ${BOLD}Starting FastAPI backend...${NC}"
  if [[ ! -d "backend" ]]; then
    echo -e "${RED}✗ backend/ not found in $ROOT${NC}"
    exit 1
  fi
  JAMBU_LLM_PROVIDER="${JAMBU_LLM_PROVIDER:-auto}" \
  JAMBU_DB_PATH="${JAMBU_DB_PATH:-$ROOT/rag_data.db}" \
    python3 -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001 --log-level info \
    > /tmp/jambu-backend.log 2>&1 &
  BACKEND_PID=$!
  sleep 2
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo -e "${RED}✗ Backend failed to start. Check /tmp/jambu-backend.log${NC}"
    tail -20 /tmp/jambu-backend.log
    exit 1
  fi
  if curl -s --max-time 3 http://127.0.0.1:8001/health > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Backend up on http://127.0.0.1:8001${NC} (PID $BACKEND_PID)"
  else
    echo -e "${YELLOW}⚠ Backend started but /health not responding yet. Continuing...${NC}"
  fi
  echo
fi

# ---------- LLM ----------
if [[ $NO_LLM -eq 0 ]]; then
  echo -e "${PURPLE}[2/3]${NC} ${BOLD}Starting LLM provider...${NC}"
  if [[ -d "mlx-venv" ]] && [[ "$(uname)" == "Darwin" ]] && [[ "$(uname -m)" == "arm64" ]]; then
    echo -e "${PURPLE}→ MLX VLM server (Apple Silicon native)${NC}"
    JAMBU_LLM_PROVIDER=mlx mlx-venv/bin/python3 backend/scripts/mlx_vlm_server.py \
      --port 8080 --model mlx-community/gemma-4-12b-it-4bit \
      > /tmp/jambu-llm.log 2>&1 &
    LLM_PID=$!
    sleep 3
    if kill -0 "$LLM_PID" 2>/dev/null; then
      echo -e "${GREEN}✓ MLX VLM server up on :8080${NC} (PID $LLM_PID)"
    else
      echo -e "${YELLOW}⚠ MLX VLM server failed to start. Check /tmp/jambu-llm.log${NC}"
      LLM_PID=
    fi
  elif command -v ollama > /dev/null 2>&1; then
    if curl -s --max-time 2 http://localhost:11434/api/tags > /dev/null 2>&1; then
      echo -e "${GREEN}✓ Ollama already running on :11434${NC}"
    else
      echo -e "${PURPLE}→ Starting Ollama (background)${NC}"
      ollama serve > /tmp/jambu-llm.log 2>&1 &
      LLM_PID=$!
      sleep 2
    fi
  else
    echo -e "${YELLOW}⚠ No LLM provider found. Install Ollama or set up MLX VLM.${NC}"
    echo -e "${YELLOW}  The app will use the Mock provider for offline demos.${NC}"
  fi
  echo
fi

# ---------- Tauri ----------
echo -e "${CYAN}[3/3]${NC} ${BOLD}Starting Tauri dev server...${NC}"
cd "$ROOT/browser-app"
npm run tauri dev > /tmp/jambu-tauri.log 2>&1 &
TAURI_PID=$!
echo -e "${GREEN}✓ Tauri dev launched${NC} (PID $TAURI_PID)"
echo
echo -e "${BOLD}Logs:${NC}"
echo "  Backend: tail -f /tmp/jambu-backend.log"
echo "  LLM:     tail -f /tmp/jambu-llm.log"
echo "  Tauri:   tail -f /tmp/jambu-tauri.log"
echo
echo -e "${BOLD}Endpoints:${NC}"
echo "  Frontend: http://localhost:1420 (Tauri webview)"
echo "  Backend:  http://127.0.0.1:8001"
echo "  Health:   http://127.0.0.1:8001/health"
echo
echo -e "${YELLOW}Press Ctrl+C to stop everything${NC}"

# Wait for any child to exit
wait -n "$BACKEND_PID" "$LLM_PID" "$TAURI_PID" 2>/dev/null || true
cleanup
