#!/usr/bin/env bash
#
# Jambubrowser macOS signing helper
# ----------------------------------
# Signs an already-built .app bundle with the configured Apple Developer ID
# and notarizes it via notarytool.
#
# Required env vars:
#   APPLE_SIGNING_IDENTITY  e.g. "Developer ID Application: Jambu AI (XXXXXXXXXX)"
#   APPLE_ID                your@apple.id
#   APPLE_PASSWORD          xxxx-xxxx-xxxx-xxxx (app-specific password from appleid.apple.com)
#   APPLE_TEAM_ID           10-character team ID
#
# Usage:  ./scripts/sign.sh path/to/Jambubrowser.app

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <path-to-Jambubrowser.app>"
  exit 1
fi

APP_PATH="$1"
if [[ ! -d "$APP_PATH" ]]; then
  echo "✗ App bundle not found: $APP_PATH"
  exit 1
fi

# ---------- Required env vars ----------
for var in APPLE_SIGNING_IDENTITY APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID; do
  if [[ -z "${!var:-}" ]]; then
    echo "✗ Required env var not set: $var"
    exit 1
  fi
done

ENTITLEMENTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/browser-app/src-tauri/entitlements.plist"

echo "════════════════════════════════════════════════════════════"
echo "  Jambubrowser macOS signing + notarization"
echo "════════════════════════════════════════════════════════════"
echo "  App:           $APP_PATH"
echo "  Identity:      $APPLE_SIGNING_IDENTITY"
echo "  Team ID:       $APPLE_TEAM_ID"
echo "  Apple ID:      $APPLE_ID"
echo "  Entitlements:  $ENTITLEMENTS"
echo

# ---------- Step 1: Sign the inner binaries ----------
echo "[1/3] Signing inner binaries (deep, strict)..."
# Sign nested code first (in reverse order of how macOS loads them)
find "$APP_PATH" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 | \
  xargs -0 -I {} codesign \
    --force \
    --options runtime \
    --timestamp \
    --entitlements "$ENTITLEMENTS" \
    --sign "$APPLE_SIGNING_IDENTITY" \
    --deep \
    "{}"

# ---------- Step 2: Sign the .app itself ----------
echo "[2/3] Signing the .app bundle..."
codesign \
  --force \
  --options runtime \
  --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$APPLE_SIGNING_IDENTITY" \
  --deep \
  "$APP_PATH"

# ---------- Step 3: Verify signature ----------
echo
echo "[3/3] Verifying signature..."
codesign --verify --verbose=4 "$APP_PATH"
echo
echo "✓ Signature valid"

# ---------- Step 4: Notarize ----------
echo
echo "Submitting for notarization..."
ZIP_PATH="${APP_PATH%.app}.zip"
echo "  Zipping: $APP_PATH → $ZIP_PATH"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

xcrun notarytool submit "$ZIP_PATH" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait

# Staple the notarization ticket to the app
xcrun stapler staple "$APP_PATH"

echo
echo "════════════════════════════════════════════════════════════"
echo "  ✓ Signed + notarized: $APP_PATH"
echo "════════════════════════════════════════════════════════════"
