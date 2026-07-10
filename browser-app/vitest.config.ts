import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // Exclude Playwright E2E tests — they live in e2e/ and depend on
    // @playwright/test which is a different runtime.
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"],
  },
});
