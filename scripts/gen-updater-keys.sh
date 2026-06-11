#!/usr/bin/env bash
#
# Jambubrowser updater keypair generator
# ---------------------------------------
# Generates a public/private keypair for tauri-plugin-updater.
# The PRIVATE key stays on the build machine (or in CI secrets).
# The PUBLIC key goes into tauri.conf.json.
#
# Usage:  ./scripts/gen-updater-keys.sh
#
# Outputs:
#   ~/.tauri/jambu-updater.key     (PRIVATE — keep secret!)
#   ~/.tauri/jambu-updater.key.pub (PUBLIC — paste into tauri.conf.json)

set -euo pipefail

KEY_DIR="${HOME}/.tauri"
PRIVATE_KEY="${KEY_DIR}/jambu-updater.key"
PUBLIC_KEY="${KEY_DIR}/jambu-updater.key.pub"

mkdir -p "$KEY_DIR"

if [[ -f "$PRIVATE_KEY" ]]; then
  echo "✗ Private key already exists at $PRIVATE_KEY"
  echo "  Refusing to overwrite. Delete it first if you want to regenerate."
  echo "  (This would invalidate all previously-signed releases!)"
  exit 1
fi

echo "Generating Tauri updater keypair..."
npx --yes @tauri-apps/cli signer generate --password "$(openssl rand -hex 32)" \
  --save-private-key "$PRIVATE_KEY"

echo
echo "════════════════════════════════════════════════════════════"
echo "  ✓ Updater keypair generated"
echo "════════════════════════════════════════════════════════════"
echo
echo "PRIVATE key:  $PRIVATE_KEY"
echo "  → Keep this secret. Store in CI secrets as TAURI_SIGNER_PRIVATE_KEY"
echo "  → DO NOT commit to git"
echo
echo "PUBLIC key:"
echo "  → Paste the contents of $PUBLIC_KEY into"
echo "    browser-app/src-tauri/tauri.conf.json under plugins.updater.pubkey"
echo
cat "$PUBLIC_KEY"
echo
echo "  And into your updater endpoint config (e.g. GitHub releases for tauri-action)."
