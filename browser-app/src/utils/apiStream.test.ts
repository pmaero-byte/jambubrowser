import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// The Tauri transport is mocked at the module boundary: `invoke` records
// calls and `Channel` captures instances so tests can push stream events.
const h = vi.hoisted(() => ({
  invoke: vi.fn(),
  channels: [] as Array<{ onmessage: ((m: unknown) => void) | null }>,
}));

vi.mock("@tauri-apps/api/core", () => {
  class Channel<T = unknown> {
    onmessage: ((m: T) => void) | null = null;
    constructor() {
      h.channels.push(this as { onmessage: ((m: unknown) => void) | null });
    }
  }
  return { invoke: h.invoke, Channel };
});

import { localFetchStream } from "./api";

function encodeBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

/** Simulate the Rust `proxy_stream` command resolving once started. */
async function startStream(path = "/v2/agent/run", init?: RequestInit) {
  h.invoke.mockResolvedValue(undefined);
  const promise = localFetchStream(path, init);
  await vi.waitFor(() => expect(h.channels.length).toBe(1));
  return { promise, channel: h.channels[0] };
}

describe("localFetchStream (Tauri transport)", () => {
  beforeEach(() => {
    h.invoke.mockReset();
    h.channels.length = 0;
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  });

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  });

  it("invokes proxy_stream with the request and stream id", async () => {
    const { promise, channel } = await startStream("/v2/agent/run", {
      method: "POST",
      body: JSON.stringify({ query: "hi" }),
    });
    channel.onmessage!({ kind: "init", status: 200, headers: {} });
    channel.onmessage!({ kind: "end" });
    await promise;

    const [cmd, args] = h.invoke.mock.calls[0] as [string, {
      streamId: string;
      request: { url: string; method: string; body: string };
    }];
    expect(cmd).toBe("proxy_stream");
    expect(args.streamId).toBeTruthy();
    expect(args.request.url).toContain("/v2/agent/run");
    expect(args.request.method).toBe("POST");
    expect(args.request.body).toBe('{"query":"hi"}');
  });

  it("exposes live chunks through the Response body", async () => {
    const { promise, channel } = await startStream();
    channel.onmessage!({
      kind: "init",
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
    channel.onmessage!({ kind: "chunk", data: encodeBase64("event: step\ndata: {}\n\n") });
    channel.onmessage!({ kind: "chunk", data: encodeBase64("event: done\ndata: {}\n\n") });
    channel.onmessage!({ kind: "end" });

    const res = await promise;
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(await res.text()).toBe("event: step\ndata: {}\n\nevent: done\ndata: {}\n\n");
  });

  it("reassembles multi-byte UTF-8 split across chunks", async () => {
    const { promise, channel } = await startStream();
    channel.onmessage!({ kind: "init", status: 200, headers: {} });
    const bytes = new TextEncoder().encode("é"); // 2 bytes
    channel.onmessage!({ kind: "chunk", data: btoa(String.fromCharCode(bytes[0])) });
    channel.onmessage!({ kind: "chunk", data: btoa(String.fromCharCode(bytes[1])) });
    channel.onmessage!({ kind: "end" });

    expect(await (await promise).text()).toBe("é");
  });

  it("propagates stream errors to the caller", async () => {
    const { promise, channel } = await startStream();
    channel.onmessage!({ kind: "error", message: "backend exploded" });
    await expect(promise).rejects.toThrow("backend exploded");
  });

  it("rejects when the invoke call itself fails", async () => {
    h.invoke.mockRejectedValue(new Error("no ipc"));
    await expect(localFetchStream("/x")).rejects.toThrow("no ipc");
  });

  it("cancels the Rust stream when the abort signal fires", async () => {
    const ac = new AbortController();
    const { promise, channel } = await startStream("/audit/run", { signal: ac.signal });
    channel.onmessage!({ kind: "init", status: 200, headers: {} });
    ac.abort();

    await expect(promise.then((r) => r.text())).rejects.toThrow(/Aborted/);
    expect(h.invoke).toHaveBeenCalledWith(
      "proxy_stream_cancel",
      expect.objectContaining({ streamId: expect.any(String) }),
    );
  });

  it("delegates to fetch when not running inside Tauri", async () => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("plain body"));
    try {
      const res = await localFetchStream("/health");
      expect(await res.text()).toBe("plain body");
      expect(h.invoke).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = original;
    }
  });
});
