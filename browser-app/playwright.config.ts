import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the Jambubrowser React app.
 *
 * NOTE: These tests run against the Vite dev server (port 1420) in
 * headless Chromium. They do NOT drive the Tauri WebView inside the
 * built .app / .dmg — the Tauri WebView is a system WebView (WKWebView
 * on macOS, WebView2 on Windows) and Playwright can't connect to it
 * directly. To drive the real desktop app, use `tauri-driver` (a
 * Selenium WebDriver implementation for Tauri) — that is a larger
 * setup not in scope here.
 *
 * What these tests DO cover:
 *   - The AppShell layout (TopBar, Sidebar, Canvas, Inspector, StatusBar)
 *   - Tab system (drag-drop, close, new) without CDP
 *   - Sidebar/inspector toggle
 *   - Command palette open/close
 *   - Theme + typography
 *
 * Run:  cd browser-app && npm run test:e2e
 *       (requires `npm run dev` to be running on :1420)
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:1420",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:1420",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
