# Jambubrowser — Tauri Desktop App

The native desktop wrapper for Jambubrowser. Wraps the React frontend in a
Tauri 2 shell with proper code signing, notarization, auto-updates, and deep
linking.

## Stack

- **Tauri 2** — native shell, ~10MB binary, Rust orchestration
- **React 19** + TypeScript + Vite — frontend (built from `frontend/jambubrowser-ui`)
- **Rust** — orchestration layer (swarm, debate, intent services)
- **Three.js** — 3D brain visualization (optional)

## Architecture

```
┌────────────────────────────────────────────────────────┐
│  Tauri Webview                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │  React UI (jambubrowser-ui)                       │  │
│  │  - Agent Timeline                                 │  │
│  │  - Memory Panel                                   │  │
│  │  - Browser Pane (iframe for local content)        │  │
│  └────────────────────┬─────────────────────────────┘  │
│                       │ HTTP                            │
└───────────────────────┼──────────────────────────────────┘
                        │ 127.0.0.1:8001
┌───────────────────────▼──────────────────────────────────┐
│  Rust Orchestrator (this app)                            │
│  - Spawns the Python backend (uvicorn) on first launch   │
│  - Spawns llama-server sidecar for offline LLM           │
│  - Manages the app lifecycle + deep links                │
└──────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- **Rust** 1.77+ — install via [rustup](https://rustup.rs)
- **Node.js** 18+
- **Xcode Command Line Tools** (macOS only) — `xcode-select --install`
- **WebKit2GTK** (Linux only) — `sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev`

### Dev mode

From the project root:
```bash
./scripts/dev.sh            # starts backend, LLM, and Tauri dev
./scripts/dev.sh --no-llm   # skip the LLM startup
./scripts/dev.sh --no-backend  # skip the backend (UI-only dev)
```

Or directly:
```bash
cd browser-app
npm install
npm run tauri dev
```

### Production build

From the project root:
```bash
./scripts/build.sh                    # auto-detect host platform
./scripts/build.sh --skip-signing     # unsigned (for local testing)
./scripts/build.sh --target aarch64-apple-darwin
```

Or directly:
```bash
cd browser-app
npm run tauri build
```

## Distribution

### macOS Code Signing

1. Get a **Developer ID Application** certificate from Apple
2. Create an **app-specific password** at https://appleid.apple.com/account/manage
3. Set environment variables:
   ```bash
   export APPLE_SIGNING_IDENTITY="Developer ID Application: Jambu AI (XXXXXXXXXX)"
   export APPLE_ID="your@apple.id"
   export APPLE_PASSWORD="xxxx-xxxx-xxxx-xxxx"
   export APPLE_TEAM_ID="XXXXXXXXXX"
   ```
4. Run `./scripts/build.sh` (or `./scripts/sign.sh <path-to-Jambubrowser.app>` to sign an existing build)

### Auto-Updates

Generate a signing keypair (one-time):
```bash
./scripts/gen-updater-keys.sh
```

This produces:
- `~/.tauri/jambu-updater.key` — **PRIVATE** (keep secret, store in CI secrets as `TAURI_SIGNING_PRIVATE_KEY`)
- `~/.tauri/jambu-updater.key.pub` — **PUBLIC** (paste into `tauri.conf.json` under `plugins.updater.pubkey`)

Auto-update endpoint: `https://github.com/pmaero-byte/jambubrowser/releases/latest/download/{{target}}/{{arch}}/{{current_version}}`
— configured in `tauri.conf.json` `plugins.updater.endpoints`.

### CI / CD

`.github/workflows/test.yml` — runs on every push: Python tests, frontend build, integration tests, linting.

`.github/workflows/release.yml` — runs on `v*` tags: matrix-builds for macOS (aarch64 + x86_64), Linux (deb/AppImage), Windows (msi/exe). Signs with Apple Developer ID, notarizes, and creates a draft GitHub Release.

`.github/dependabot.yml` — weekly dependency updates for Python, npm, Cargo, and GitHub Actions.

## Project Structure

```
browser-app/
├── src/                       # React frontend source
│   ├── components/
│   ├── App.tsx
│   └── main.tsx
├── src-tauri/                 # Rust + Tauri config
│   ├── src/
│   │   ├── commands/          # UI-callable Rust functions
│   │   ├── orchestrator/      # Swarm, debate, intent, services
│   │   ├── lib.rs             # Plugin registration + setup
│   │   └── main.rs
│   ├── capabilities/
│   │   └── default.json       # Window permissions
│   ├── icons/
│   ├── binaries/              # llama-server sidecar
│   ├── Cargo.toml
│   ├── Info.plist             # macOS bundle metadata
│   ├── entitlements.plist     # macOS code signing entitlements
│   ├── build.rs
│   └── tauri.conf.json        # Main config (CSP, window, bundle, updater)
├── public/
├── package.json
├── vite.config.ts
└── README.md
```

## Available Plugins

| Plugin | Purpose |
|--------|---------|
| `tauri-plugin-opener` | Open URLs / files in default app |
| `tauri-plugin-shell` | Spawn child processes (Python backend, llama-server) |
| `tauri-plugin-updater` | Auto-update mechanism |
| `tauri-plugin-notification` | Native OS notifications |
| `tauri-plugin-process` | Exit / relaunch the app |
| `tauri-plugin-deep-link` | `jambubrowser://` URL scheme |

## Custom URL Scheme

`jambubrowser://` URLs are handled by the app:

- `jambubrowser://research?q=<query>` — open a research query
- `jambubrowser://memory` — open the memory panel
- `jambubrowser://settings` — open settings

Configure in `tauri.conf.json` `bundle.macOS` and `Info.plist` `CFBundleURLTypes`.

## Troubleshooting

**"llama-server not found"** — download the sidecar binary to `src-tauri/binaries/`. See `scripts/dev.sh` for the recommended MLX setup on Apple Silicon.

**"Code signing identity not found"** — set `APPLE_SIGNING_IDENTITY` env var, or run with `--skip-signing` for unsigned dev builds.

**"CSP violation"** — update the `csp` field in `tauri.conf.json`. The current policy allows localhost (for backend), HTTPS, and inline styles (for Framer Motion).

**Auto-update fails** — check the `pubkey` in `tauri.conf.json` matches your generated key. The build that produced the release must have been signed with the matching private key.

## License

© 2026 Jambu AI. All rights reserved.
