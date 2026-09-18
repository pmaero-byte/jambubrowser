# Multi-webview tabs — architecture plan

## Why this is a milestone, not a patch

Today each "tab" is a CDP target inside one headless Chromium process; the
pane renders pushed JPEG frames (`Page.startScreencast`) and forwards input
over CDP. That gives automation parity (every pixel the agent sees is the
real page) at the cost of native feel: no OS text caret, no native context
menus, no drag-and-drop files, no plugins, no per-tab process isolation.

Tauri v2 *can* host multiple `Webview`s in one window — but those are the
**system webviews** (WKWebView / WebView2 / WebKitGTK), not Chromium. A
"native tab" would fork the product: different engine, no CDP input/audit
parity, no fingerprint control, different behavior per OS. That tradeoff must
be explicit before any code is written.

## Recommendation: dual-mode tabs, not an engine swap

Keep the CDP Chromium as the automation/autit source of truth, and add a
per-tab **view mode**:

| Mode | Renderer | Input | Best for |
|---|---|---|---|
| `stream` (current) | JPEG frames + CDP forward | CDP dispatch | automation, audits, agents |
| `native` (new) | System `Webview` child | Native | reading, forms, CAPTCHA, downloads |

A native tab cannot be audited by CDP and cannot share the fingerprint
profile; the UI must say so. The two modes answer different jobs.

## Phases

### Phase 1 — spike (1–2 days, throwaway-able)
- `browser-app/src-tauri`: proof that a `Webview` child can be created,
  positioned over the viewport rect, navigated, and destroyed from a command
  (`browser_native_view {tabId, url, rect}` / `browser_native_close`).
- Measure: creation latency, resize smoothness, macOS/Windows parity.
- **Decision gate:** if resize/focus behavior is janky, stop — the UX cost
  exceeds the gain.

### Phase 2 — tab model (3–5 days)
- `appStore`: `viewMode: "stream" | "native"` per tab; URL sync both ways
  (native webview URL-change events → address bar; address bar → native nav).
- ChromiumPane: mount/unmount the native view on mode switch and tab switch;
  pause the screencast while native is mounted (save CPU/bandwidth).
- Capabilities matrix in the UI: audit/devtools buttons disabled with an
  explanatory tooltip in native mode.

### Phase 3 — parity shims (1 week)
- Downloads, find-in-page, and copy-text routed per mode (native uses
  webview APIs; stream uses the existing CDP paths).
- Per-mode keyboard shortcut behavior; focus management between React chrome
  and the native child.
- Persistence of mode preference per domain.

### Phase 4 — hardening (1 week)
- Process model: what happens when the native webview crashes (fallback to
  stream mode automatically).
- Security review: system webviews don't inherit the fingerprint/privacy
  scripts — document exactly what is and isn't protected in native mode.
- E2E: Playwright tests driving both modes (the existing vitest suite plus
  `cargo test` for the rect math).

## Non-goals (explicit)

- Replacing the CDP Chromium: automation, audits, fingerprint rotation, and
  the agent session model depend on it.
- Plugin support in native tabs (system webviews don't load Chrome extensions).
- Pixel-perfect parity between modes (document the differences instead).

## Status

**Phase 2 shipped (2026-09).** Dual-mode tabs are implemented:

- Rust: `ChromiumPane` can host a native system-webview child over the
  viewport rect via `browser_native_view` / `browser_native_set_rect` /
  `browser_native_url` / `browser_native_close` / `browser_native_action`
  (`browser-app/src-tauri/src/chromium/native_view.rs`, requires Tauri's
  `unstable` feature for `Window::add_child`).
- Frontend: `useNativeView` mounts/resizes/polls the child and tears it down
  on tab switch/close; the pane has a per-tab view toggle (layers icon) with
  a "Native view — system engine, no audits" badge; URL, reload, back, and
  forward route through the native child in native mode.
- The CDP stream view is the default and is paused while a native child is
  mounted.

**Not yet done:** Phase 1's measured spike (latency/resize benchmarking on a
real desktop build), Phase 3 parity shims beyond navigation (downloads,
find-in-page, copy-text currently use the CDP/screenshot path and are
disabled in native mode), and Phase 4 hardening (crash fallback, security
review of what native mode does *not* inherit — fingerprint/privacy scripts).
The mode is opt-in per tab and clearly labelled; automation and audits
continue to run against the CDP tab.

The screencast live view + takeover toggle + copy-text remain the streaming
mode's feature set.
