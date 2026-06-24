/**
 * Real-LLM integration test — browser-app side.
 *
 * Mirrors the backend real-LLM test gate (tests/test_real_llm_integration.py):
 * when JAMBU_LLM_PROVIDER points at a real provider AND the engine is up,
 * hit the engine's /v2/llm/chat endpoint and assert the response shape.
 * Otherwise skip gracefully.
 *
 * Note: this test runs in node (vitest) and uses raw `fetch` rather than
 * the `localFetch` wrapper, because `localFetch` assumes a browser/Dev-mode
 * environment (Tauri proxy or Vite dev proxy) that doesn't exist in node.
 * In a real browser context, the same call would be made through
 * `localFetch('/v2/llm/chat', ...)`.
 */

import { describe, it, expect } from "vitest";

// Read env from Vite's import.meta.env first (browser), then fall back to
// process.env (vitest runs in node). Use globalThis indirection to avoid
// the @types/node dependency (the browser app has no node types).
const _g: any = globalThis as any;
const procEnv: Record<string, string | undefined> = (_g && _g.process && _g.process.env) || {};

const ENGINE_URL: string =
  (import.meta.env as any).VITE_ENGINE_URL ||
  procEnv.JAMBU_ENGINE_URL ||
  "http://127.0.0.1:8001";

const hasRealProvider: boolean | undefined = (() => {
  const v = procEnv.JAMBU_LLM_PROVIDER;
  if (v) return v !== "mock";
  return undefined;
})();

async function engineReachable(): Promise<boolean> {
  try {
    const r = await fetch(`${ENGINE_URL}/health`, {
      signal: AbortSignal.timeout(2000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

describe("real-LLM integration", () => {
  it(
    "engine /v2/llm/chat returns a real model response when a real provider is configured",
    async () => {
      if (hasRealProvider === false) {
        console.log("[skip] JAMBU_LLM_PROVIDER=mock");
        return;
      }
      if (!(await engineReachable())) {
        console.log(`[skip] engine not reachable at ${ENGINE_URL}`);
        return;
      }

      // Use raw fetch (see file header).
      const res = await fetch(`${ENGINE_URL}/v2/llm/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", content: "Reply with the single word 'pong'." }],
          max_tokens: 200,
          temperature: 0,
        }),
        signal: AbortSignal.timeout(20_000),
      });
      expect(res.ok).toBe(true);
      const data = await res.json();
      expect(data).toBeTruthy();
      expect(typeof data.content).toBe("string");
      expect(data.content.length).toBeGreaterThan(0);
      expect(data.usage).toBeTruthy();
      expect(data.usage.total_tokens).toBeGreaterThan(0);
    },
    30_000,
  );

  it("engine /health returns ok when up", async () => {
    if (!(await engineReachable())) {
      console.log("[skip] engine not reachable");
      return;
    }
    const res = await fetch(`${ENGINE_URL}/health`);
    expect(res.ok).toBe(true);
    const data = await res.json();
    expect(data.status).toBe("online");
  }, 5_000);
});

