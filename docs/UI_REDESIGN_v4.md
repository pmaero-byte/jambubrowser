# Jambubrowser UI/UX Redesign v4 — State-of-the-Art AI Dashboard

**Date:** June 2026  
**Scope:** Replace the current dual-frontend confusion (`frontend/jambubrowser-ui/` + `browser-app/src/`) with a single, cohesive, grade-A interface for the Tauri desktop app and a matching web build.

---

## 1. Why a redesign?

The repo currently ships **two unrelated React frontends**:

- `frontend/jambubrowser-ui/` — standalone web UI (React 18 + Vite 5173)
- `browser-app/src/` — Tauri 2 shell UI (React 19 + Vite 1420, Three.js, AgentRoom)

They share no components, no design tokens, and no state. The Tauri README incorrectly claims it is "built from `frontend/jambubrowser-ui`". This fork is unsustainable and blocks a consistent product experience.

**Goal:** converge both surfaces on one codebase, one design system, and one mental model.

---

## 2. Recommended Stack (2026)

| Layer | Choice | Rationale |
|---|---|---|
| Framework | **React 19 + Vite 7** | Already used in `browser-app/`. Keep the newer baseline, drop the React 18 app. |
| Components | **shadcn/ui v4** | Copy-paste ownership, Tailwind v4 native, 117k stars, AI-agent dashboards default. |
| AI Primitives | **Vercel AI Elements** | Conversation, Message, Reasoning, Tool, PromptInput — built on shadcn. |
| Command Bar | **cmdk** | Headless, ARIA-correct, 33.8M weekly downloads. |
| Motion | **Motion (Framer Motion)** | Shared layout animations, `useReducedMotion`, springs. |
| Styling | **Tailwind v4 + OKLCH tokens** | CSS-first `@theme inline`, P3 gamut, dark-mode-ready. |
| State | **Zustand** | Lightweight, used by leading agent dashboards. |
| Data/Streams | **TanStack Query + AI SDK** | Resumable SSE streams. |
| Forms | **React Hook Form + Zod** | shadcn standard pairing. |
| Icons | **Lucide** | Already in both frontends. |
| a11y testing | **axe-core + manual screen-reader smoke** | Required for agent chat compliance. |

---

## 3. Target Layout: The 4-Pane Agent Shell

```
┌─────────────────────────────────────────────────────────────────┐
│ TopBar    [Workspace] [Model selector] [Cost/privacy pill] [⌘K] │
├────────┬──────────────────────────────────────────┬─────────────┤
│        │                                          │             │
│ Side   │   Main Canvas                            │  Inspector  │
│ bar    │   • Chat / Plan / Browser / Logs         │  • Artifact │
│        │                                          │    preview  │
│        │                                          │  • Diff     │
│        │                                          │  • Run cfg  │
│        │                                          │             │
└────────┴──────────────────────────────────────────┴─────────────┘
│ StatusBar   [Tokens/s] [Cost] [Provider] [Privacy mode] [Health] │
└─────────────────────────────────────────────────────────────────┘
```

### Pane responsibilities

1. **TopBar** — global identity: workspace/project picker, active model, privacy mode pill, cost tracker, command palette trigger (`⌘K`), settings.
2. **Sidebar** — persistent navigation + context. Collapsible with `⌘B`. Sections:
   - **Workspace:** sessions, missions, memory, knowledge graph
   - **Create:** new research, new tab, schedule mission
   - **History:** past runs, starred outputs
   - **Settings:** pinned to bottom (privacy, vault, audit log)
3. **Main Canvas** — dynamic surface with tabs for:
   - **Research Chat** — streaming messages, tool calls, sources
   - **Plan Surface** — pre-execution step list with approve/skip/edit
   - **Browser** — iframe/browser view for source pages
   - **Logs** — live audit + agent telemetry
4. **Inspector** — contextual detail panel:
   - Selected source / extracted data
   - Active tool call args/results
   - Memory matches for current query
   - 3D knowledge-graph mini-view
5. **StatusBar** — real-time throughput, provider health, privacy mode, vault lock state.

---

## 4. Component Inventory

### shadcn/ui base blocks

| Component | File location (new) | Purpose |
|---|---|---|
| `app-shell.tsx` | `src/components/layout/AppShell.tsx` | TopBar + Sidebar + Canvas + Inspector + StatusBar |
| `sidebar.tsx` | `src/components/layout/Sidebar.tsx` | shadcn Sidebar primitive, collapsible, `⌘B` |
| `command-palette.tsx` | `src/components/command/CommandPalette.tsx` | cmdk wrapper: navigation + actions + MCP tools |
| `agent-timeline.tsx` | `src/components/agent/AgentTimeline.tsx` | Live plan → execute → verify → replan steps |
| `chat-pane.tsx` | `src/components/chat/ChatPane.tsx` | Vercel AI Elements `Conversation` + `PromptInput` |
| `message-card.tsx` | `src/components/chat/MessageCard.tsx` | User/assistant message with source chips, reasoning fold |
| `browser-pane.tsx` | `src/components/browser/BrowserPane.tsx` | URL bar + iframe/browser view + action controls |
| `inspector-panel.tsx` | `src/components/inspector/InspectorPanel.tsx` | Context-aware right panel |
| `privacy-controls.tsx` | `src/components/privacy/PrivacyControls.tsx` | 4-mode selector + URL check |
| `vault-unlock.tsx` | `src/components/vault/VaultUnlock.tsx` | Unlock + domain list |
| `audit-log-viewer.tsx` | `src/components/audit/AuditLogViewer.tsx` | Live audit stream |
| `metrics-strip.tsx` | `src/components/status/MetricsStrip.tsx` | Tokens/s, cost, latency |
| `memory-panel.tsx` | `src/components/memory/MemoryPanel.tsx` | Hybrid recall results |
| `knowledge-mini.tsx` | `src/components/knowledge/KnowledgeMini.tsx` | 2D/3D graph preview |
| `onboarding-wizard.tsx` | `src/components/onboarding/OnboardingWizard.tsx` | First-run + `?` reopen |

### Vercel AI Elements integration

Install per component:

```bash
npx shadcn@latest add https://elements.ai-sdk.dev/r/conversation.json
npx shadcn@latest add https://elements.ai-sdk.dev/r/message.json
npx shadcn@latest add https://elements.ai-sdk.dev/r/prompt-input.json
npx shadcn@latest add https://elements.ai-sdk.dev/r/reasoning.json
npx shadcn@latest add https://elements.ai-sdk.dev/r/tool.json
```

Use `Streamdown` for the tail message (`mode="streaming"` active, `mode="static"` once committed).
Use `StickToBottom` for auto-scroll during streaming.

---

## 5. Design Tokens (OKLCH)

File: `src/styles/globals.css`

```css
@import "tailwindcss";
@import "tw-animate-css";
@custom-variant dark (&:is(.dark *));

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-primary: var(--primary);
  --color-secondary: var(--secondary);
  --color-muted: var(--muted);
  --color-accent: var(--accent);
  --color-border: var(--border);
  --color-ring: var(--ring);
  --radius-sm: 0.5rem;
  --radius-md: 0.75rem;
  --radius-lg: 1rem;
}

:root {
  --background: oklch(0.985 0.007 65);
  --foreground: oklch(0.155 0.015 55);
  --primary: oklch(0.21 0.02 265);
  --secondary: oklch(0.955 0.01 65);
  --muted: oklch(0.92 0.01 65);
  --accent: oklch(0.55 0.18 265);
  --border: oklch(0.85 0.01 65);
  --ring: oklch(0.708 0.015 265);
}

.dark {
  --background: oklch(0.145 0 0);
  --foreground: oklch(0.985 0 0);
  --primary: oklch(0.922 0 0);
  --secondary: oklch(0.22 0 0);
  --muted: oklch(0.24 0 0);
  --accent: oklch(0.65 0.18 265);
  --border: oklch(1 0 0 / 10%);
  --ring: oklch(0.708 0.015 265);
}
```

Accent candidates for Jambubrowser:
- **Lavender** `#5e6ad2` (Linear) — calm, research-grade
- **Cyan** `#00d4ff` — futuristic, agentic

Recommendation: lavender primary + cyan accent for agent state/Streaming.

---

## 6. Motion Tokens

| Interaction | Duration | Easing | Note |
|---|---|---|---|
| Hover | 150–200ms | ease-out | scale 1.02, opacity 0.8 |
| Tab switch | 200ms | spring (stiffness 400, damping 17) | layoutId shared |
| Sidebar collapse | 250ms | spring | GPU-only transform |
| Modal enter | 250ms | spring (300, 30) | scale 0.95→1 |
| Modal exit | 150ms | ease-in | faster than enter |
| Toast | 200ms enter / 150ms exit | spring | translateX 100%→0 |
| Stream token | per-token shimmer | — | no DOM thrash, batch ≤2/frame |

**Rules:**
- Animate only `transform` and `opacity`.
- Use `useReducedMotion()` to disable transforms.
- Use `layoutId` for active nav pill and expanding cards.
- Wrap exit animations in `<AnimatePresence>`.

---

## 7. Accessibility Checklist

### Command palette
- `role="combobox"`, `aria-autocomplete="list"`, `aria-expanded`, `aria-controls`, `aria-activedescendant`
- `role="listbox"` + `role="option"` for results
- `aria-live="polite"` announcing result count
- `Esc` closes, focus returns to trigger

### Chat stream
- `role="log"`, `aria-live="polite"` for new messages
- Do **not** steal focus on new assistant messages
- Throttle screen-reader announcements; never announce every token
- `aria-busy="true"` on streaming message
- Skip-to-input link for keyboard users

### General
- Visible focus rings (`focus-visible:ring-2 focus-visible:ring-ring`)
- All interactive elements reachable by Tab
- Dialogs trap focus and restore on close
- Color contrast ≥ 4.5:1 (APCA Lc ≥ 75 for body)
- `prefers-reduced-motion: reduce` fallback
- `prefers-contrast: more` support
- Use `rem`, respect OS font size

---

## 8. Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `⌘/Ctrl + K` | Open command palette |
| `⌘/Ctrl + B` | Toggle sidebar |
| `⌘/Ctrl + L` | Focus logs / audit |
| `⌘/Ctrl + Shift + M` | Toggle memory panel |
| `⌘/Ctrl + T` | New browser tab |
| `⌘/Ctrl + Shift + P` | Privacy controls |
| `⌘/Ctrl + ?` | Keyboard help / onboarding |
| `Esc` | Close palette / modal / inspector |

---

## 9. Migration Plan

### Phase 1 — Scaffold (week 1)
1. In `browser-app/`, replace the React 19 app with a fresh Vite 7 + shadcn v4 init.
2. Copy stable utility code from `frontend/jambubrowser-ui/src/utils/` (API hooks, keyboard shortcuts, types).
3. Delete `frontend/jambubrowser-ui/` once Tauri app is the canonical UI.

### Phase 2 — Layout (week 1–2)
1. Build `AppShell`, `Sidebar`, `TopBar`, `StatusBar`.
2. Port `AgentTimeline` and `ChatPane` using Vercel AI Elements.
3. Add `BrowserPane` with iframe sandbox + action controls.

### Phase 3 — Features (week 2–3)
1. Privacy controls, vault unlock, audit log, memory panel.
2. Inspector panel with knowledge-graph mini-view.
3. Command palette wired to all routes.

### Phase 4 — Polish (week 3–4)
1. Motion tokens, reduced-motion support.
2. a11y audit (axe + VoiceOver).
3. Theme switch (system/light/dark).
4. Responsive breakpoints (sidebar collapses to sheet on narrow widths).

### Phase 5 — Web build parity
1. Build `browser-app` to `dist/`.
2. Add a separate `vite build --mode web` target that produces `frontend/jambubrowser-ui/dist/` for the README’s web quick-start.
3. Update CI in `.github/workflows/test.yml` to build the single app.

---

## 10. What to keep from existing frontends

Keep from `frontend/jambubrowser-ui/src/utils/`:
- `api.ts` — `localFetch` / `isTauri`
- `useAgentWebSocket.ts` — WebSocket hook
- `useKeyboardShortcuts.ts` — shortcut hook
- `types.ts` — `AgentEvent`

Keep from `browser-app/src/`:
- Three.js brain graph component (repackage as `KnowledgeMini`)
- Tauri orchestrator integration (`isTauri` checks, deep-link handling)
- Real-time telemetry SSE consumer

Discard:
- `frontend/jambubrowser-ui/src/App.tsx` and its 27 KB CSS
- `browser-app/src/App.tsx` 823-line monolith
- Duplicate component sets
- "GOD MODE" marketing copy in favor of neutral "Advanced" labels

---

## 11. Open Questions for Product Owner

1. Should the web build and desktop build share **exactly** the same UI, or should the desktop build expose OS-native features (notifications, global hotkeys) that the web build hides?
2. Which accent color should anchor the brand: lavender (Linear-like) or cyan (agentic)?
3. Should the 3D brain graph remain Three.js, or move to a lighter 2D canvas/graph library for battery life?
4. Do we want per-workspace theming, or a single global dark/light toggle?

---

## 12. References

- shadcn/ui v4 sidebar: https://ui.shadcn.com/docs/components/base/sidebar
- shadcn/ui command: https://ui.shadcn.com/docs/components/base/command
- Vercel AI Elements: https://github.com/vercel/ai-elements
- cmdk: https://github.com/pacocoursey/cmdk
- Motion: https://motion.dev/
- Brainy 7 agent UI patterns: https://brainy.ink/paper/ai-agent-ui-design-patterns
- CallSphere a11y for agent chat: https://callsphere.ai/blog/accessibility-agent-chat-interfaces-screen-readers-focus-aria
