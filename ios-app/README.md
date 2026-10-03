# Jambubrowser iOS

The iOS client is a **thin SwiftUI front-end** for a Jambubrowser engine
(the desktop app, the CLI daemon, or a self-hosted instance) running
elsewhere. It never runs the engine itself — it talks to the engine's
`/v1/*` compatibility API and renders live state.

## Layout

```
ios-app/
  project.yml                  # XcodeGen spec (source of truth)
  JambubrowserKit/             # Shared framework: API models, keychain, widget client
  Jambubrowser/                # SwiftUI app target
    JambubrowserApp.swift      # Entry point, DI, scene phase
    Services/                  # AppState, GatewayClient, notifications, cache models
    Views/                     # Dashboard, Run, Console, Sessions, Settings
    Background/                # BGTaskScheduler registrations
```

`Jambubrowser.xcodeproj` is **generated** from `project.yml`:

```bash
cd ios-app
xcodegen generate
```

## Build and run (simulator)

```bash
cd ios-app
xcodegen generate
xcodebuild -project Jambubrowser.xcodeproj -scheme Jambubrowser \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
xcrun simctl install booted <path-to>/Jambubrowser.app
xcrun simctl launch booted com.jambubrowser.ios
```

The simulator shares the host network, so `http://localhost:8001` reaches
the engine running on your Mac.

## Run on a physical iPhone

1. Start the engine on your Mac bound to the LAN:
   ```bash
   JAMBU_ENABLE_DECENTRALIZED=1 \
   ALLOWED_HOSTS="localhost,127.0.0.1,0.0.0.0,<your-lan-ip>" \
   .venv/bin/python -m uvicorn backend.engine:app --host 0.0.0.0 --port 8001
   ```
   (`ALLOWED_HOSTS` matters: the engine's trusted-host middleware rejects
   requests whose `Host` header is not allow-listed.)
2. In Xcode: open `ios-app/Jambubrowser.xcodeproj`, select the **Jambubrowser**
   target → Signing & Capabilities → your Apple ID / team, pick your iPhone
   in the run destination, press Run.
3. On first launch open **More → Settings**, set the gateway URL to
   `http://<your-lan-ip>:8001`, and tap **Test Connection**.

## What is verified today

- Builds clean for iOS 17+ simulator and device SDKs (`xcodebuild`, Xcode 27)
- Installs and launches on the iPhone 17 Pro simulator
- Dashboard renders live engine data: connection state, connector inventory
  (`/v1/connectors`), model list (`/v1/models`), session list (`/v1/sessions`)
- Five tabs wired: Dashboard, Run, Console, Sessions, More (Settings)

## Honest gaps (not yet implemented server-side)

- `/v1/mcp/servers` has no backend route — MCP server management in Settings
  reports an empty catalogue rather than pretending
- `runParallel` falls back to a single `/v1/run` and synthesizes the
  comparison client-side until a `/v1/run/parallel` route exists
- `runStream` uses the non-streaming `/v1/run`; SSE parsing is not wired
- Live Activities / Handoff / Spotlight / widgets are permission-gated shells
  awaiting their ActivityKit/WidgetKit extension targets
