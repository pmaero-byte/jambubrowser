#!/usr/bin/env bash
#
# Jambubrowser local production build
# -----------------------------------
# Builds signed (where possible) Tauri installers for the current platform.
#
# Usage:
#   ./scripts/build.sh                  # Auto-detect, build for current platform
#   ./scripts/build.sh --target aarch64-apple-darwin  # Cross-compile
#   ./scripts/build.sh --skip-signing   # Build only, skip macOS signing/notarization
#   ./scripts/build.sh --debug          # Debug build
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/browser-app"

# ---------- Args ----------
SKIP_SIGNING=0
DEBUG=0
TARGET=""
for arg in "$@"; do
  case "$arg" in
    --skip-signing) SKIP_SIGNING=1 ;;
    --debug) DEBUG=1 ;;
    --target) shift; TARGET="${1:-}" ;;
    -h|--help)
      echo "Usage: $0 [--skip-signing] [--debug] [--target <triple>]"
      exit 0
      ;;
  esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${CYAN}╭───────────────────────────────────────╮${NC}"
echo -e "${BOLD}${CYAN}│   Jambubrowser production build      │${NC}"
echo -e "${BOLD}${CYAN}╰───────────────────────────────────────╯${NC}"
echo

# ---------- Pre-flight ----------
if ! command -v cargo > /dev/null 2>&1; then
  echo -e "${RED}✗ cargo not found. Install Rust: https://rustup.rs${NC}"
  exit 1
fi
if ! command -v node > /dev/null 2>&1; then
  echo -e "${RED}✗ node not found. Install Node.js 18+${NC}"
  exit 1
fi
if ! command -v npm > /dev/null 2>&1; then
  echo -e "${RED}✗ npm not found.${NC}"
  exit 1
fi

# ---------- MacOS signing ----------
MACOS_SIGN_CMD=""
if [[ "$(uname)" == "Darwin" ]] && [[ $SKIP_SIGNING -eq 0 ]]; then
  if [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]] && [[ -n "${APPLE_ID:-}" ]] && [[ -n "${APPLE_PASSWORD:-}" ]] && [[ -n "${APPLE_TEAM_ID:-}" ]]; then
    echo -e "${GREEN}✓ macOS signing credentials detected${NC}"
    MACOS_SIGN_CMD="tauri build --target ${TARGET:-aarch64-apple-darwin}"
    export APPLE_SIGNING_IDENTITY
    export APPLE_ID
    export APPLE_PASSWORD
    export APPLE_TEAM_ID
  else
    echo -e "${YELLOW}⚠ macOS signing not configured. To enable:${NC}"
    echo "    export APPLE_SIGNING_IDENTITY=\"Developer ID Application: Jambu AI (XXXXXXXXXX)\""
    echo "    export APPLE_ID=\"your@apple.id\""
    echo "    export APPLE_PASSWORD=\"xxxx-xxxx-xxxx-xxxx\"  # app-specific password"
    echo "    export APPLE_TEAM_ID=\"XXXXXXXXXX\""
    echo "  Continuing with unsigned build..."
    echo
  fi
fi

# ---------- npm install ----------
if [[ ! -d node_modules ]]; then
  echo -e "${CYAN}[1/4]${NC} ${BOLD}Installing npm dependencies...${NC}"
  npm install
  echo -e "${GREEN}✓ npm dependencies installed${NC}"
  echo
fi

# ---------- Tauri build ----------
DEBUG_FLAG=""
[[ $DEBUG -eq 1 ]] && DEBUG_FLAG="--debug"

echo -e "${CYAN}[2/4]${NC} ${BOLD}Building Tauri app...${NC}"
echo -e "Target: ${BOLD}${TARGET:-auto (host)}${NC}"
echo

if [[ -n "$MACOS_SIGN_CMD" ]]; then
  eval "$MACOS_SIGN_CMD $DEBUG_FLAG"
else
  if [[ -n "$TARGET" ]]; then
    npm run tauri -- build --target "$TARGET" $DEBUG_FLAG
  else
    npm run tauri -- build $DEBUG_FLAG
  fi
fi

# ---------- Locate artifacts ----------
echo
echo -e "${CYAN}[3/4]${NC} ${BOLD}Locating build artifacts...${NC}"

case "$(uname -s)" in
  Darwin)
    APP_PATH="src-tauri/target/release/bundle/macos/Jambubrowser.app"
    DMG_DIR="src-tauri/target/release/bundle/dmg"
    if [[ -d "$APP_PATH" ]]; then
      echo -e "${GREEN}✓ App bundle: $APP_PATH${NC}"
    fi
    if [[ -d "$DMG_DIR" ]]; then
      echo -e "${GREEN}✓ DMG installer(s):${NC}"
      ls -lh "$DMG_DIR"/*.dmg 2>/dev/null | awk '{print "    " $9, "(" $5 ")"}'
    fi
    ;;
  Linux)
    DEB_DIR="src-tauri/target/release/bundle/deb"
    RPM_DIR="src-tauri/target/release/bundle/rpm"
    APPIMAGE_DIR="src-tauri/target/release/bundle/appimage"
    [[ -d "$DEB_DIR"      ]] && ls -lh "$DEB_DIR"/*.deb      2>/dev/null | awk '{print "    DEB:      " $9, "(" $5 ")"}'
    [[ -d "$RPM_DIR"      ]] && ls -lh "$RPM_DIR"/*.rpm      2>/dev/null | awk '{print "    RPM:      " $9, "(" $5 ")"}'
    [[ -d "$APPIMAGE_DIR" ]] && ls -lh "$APPIMAGE_DIR"/*.AppImage 2>/dev/null | awk '{print "    AppImage: " $9, "(" $5 ")"}'
    ;;
  MINGW*|MSYS*|CYGWIN*|Windows*)
    MSI_DIR="src-tauri/target/release/bundle/msi"
    NSIS_DIR="src-tauri/target/release/bundle/nsis"
    [[ -d "$MSI_DIR"  ]] && ls -lh "$MSI_DIR"/*.msi  2>/dev/null | awk '{print "    MSI:  " $9, "(" $5 ")"}'
    [[ -d "$NSIS_DIR" ]] && ls -lh "$NSIS_DIR"/*.exe 2>/dev/null | awk '{print "    NSIS: " $9, "(" $5 ")"}'
    ;;
esac

echo
echo -e "${CYAN}[4/4]${NC} ${BOLD}Build complete!${NC}"
echo
echo -e "${BOLD}Next steps:${NC}"
echo "  1. Test the installer locally"
echo "  2. Tag the release:  git tag v3.0.0 && git push --tags"
echo "  3. GitHub Actions will build + sign all platforms and publish a draft release"
echo "  4. Manually promote the draft to a public release after smoke testing"
echo
