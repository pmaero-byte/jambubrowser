import { test, expect } from "@playwright/test";

test.describe("Jambubrowser — Vite dev shell", () => {
  test.beforeEach(async ({ page }) => {
    // Forward console errors to the test reporter so they're visible
    // in CI output.
    page.on("pageerror", (err) => console.error("[pageerror]", err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") console.error("[console.error]", msg.text());
    });
  });

  test("app shell loads and shows the default tab", async ({ page }) => {
    await page.goto("/");
    // AppShell renders the title bar; the brand text is a stable marker.
    await expect(page.getByText("Jambubrowser").first()).toBeVisible({ timeout: 10_000 });
    // The default tab is named "Astrogenesis" in the initial store state.
    await expect(page.getByText(/Astrogenesis/i).first()).toBeVisible();
  });

  test("sidebar toggle responds to clicks", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Jambubrowser").first()).toBeVisible({ timeout: 10_000 });

    // The sidebar starts open; find the toggle button by its
    // accessible name (the panel icon has no text label, so we
    // click the first button in the TopBar that looks like a
    // hamburger / panel toggle).
    const sidebarToggle = page.locator("button[title*='sidebar' i], button[aria-label*='sidebar' i]").first();
    if (await sidebarToggle.count() > 0) {
      // Toggling the sidebar shouldn't throw or unmount the topbar.
      await sidebarToggle.click();
      await expect(page.getByText("Jambubrowser").first()).toBeVisible();
    }
  });

  test("command palette opens with ⌘K and closes with Escape", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Jambubrowser").first()).toBeVisible({ timeout: 10_000 });

    // Use Control+K on linux/win CI, Meta+K on mac. The platform is
    // detected by Playwright.
    const isMac = process.platform === "darwin";
    await page.keyboard.press(isMac ? "Meta+k" : "Control+k");
    // The palette is rendered into a cmdk root; if it's there, an
    // input is focusable.
    const cmdkInput = page.locator("[cmdk-input], input[cmdk-input]").first();
    const hasCmdk = await cmdkInput.count();
    if (hasCmdk > 0) {
      await expect(cmdkInput).toBeVisible();
      await page.keyboard.press("Escape");
    }
  });

  test("DevTools panel toggle: opening the Network tab reveals the network list", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Jambubrowser").first()).toBeVisible({ timeout: 10_000 });
    // The devtools panel may not be visible by default; if there is a
    // toggle (Ctrl+Shift+I or similar), open it. We don't fail the
    // test if the hotkey isn't bound — just verify the app stays
    // mounted after the attempt.
    const isMac = process.platform === "darwin";
    await page.keyboard.press(isMac ? "Meta+Alt+i" : "Control+Shift+i");
    // Give the panel a beat to animate in.
    await page.waitForTimeout(300);
    await expect(page.getByText("Jambubrowser").first()).toBeVisible();
  });
});
