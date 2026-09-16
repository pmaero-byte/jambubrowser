// Unified API transport — works in web builds and inside the Tauri WebView.
// In Tauri, HTTP is routed through the Rust `proxy_localhost` command so the
// WebView CSP (which blocks arbitrary connect-src) stays tight. Streaming
// (SSE) responses use `proxy_stream`, which forwards chunks as they arrive
// instead of buffering the whole body.

import { invoke, Channel } from "@tauri-apps/api/core";

const DEFAULT_TIMEOUT_MS = 30000;

export function isTauri(): boolean {
  return typeof window !== "undefined" && !!(window as any).__TAURI_INTERNALS__;
}

function backendOrigin(): string {
  return import.meta.env.DEV ? "" : "http://localhost:8001";
}

/**
 * Web build: performs a normal fetch to localhost:8001 (or via Vite proxy in dev).
 * Tauri build: forwards the request through the Rust proxy_localhost command.
 *
 * opts.timeoutMs overrides the default 30s abort (long browser runs like
 * session-record replay legitimately need minutes). Only the web build
 * honors it — the Tauri proxy has no client-side timeout.
 */
export async function localFetch(
  path: string,
  options?: RequestInit,
  opts?: { timeoutMs?: number },
): Promise<Response> {
  const url = `${backendOrigin()}${path}`;

  if (isTauri()) {
    const method = (options?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options?.headers as Record<string, string> || {}),
    };
    const body = options?.body
      ? typeof options.body === "string"
        ? options.body
        : JSON.stringify(options.body)
      : undefined;

    const result = await invoke<{
      status: number;
      headers: Record<string, string>;
      body: string;
    }>("proxy_localhost", {
      url,
      method,
      headers,
      body,
    });

    return new Response(result.body, {
      status: result.status,
      headers: result.headers,
    });
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...options,
      signal: combineSignals(controller.signal, options?.signal),
      headers: {
        "Content-Type": "application/json",
        ...(options?.headers as Record<string, string> || {}),
      },
    });
  } finally {
    clearTimeout(timer);
  }
}

function combineSignals(a: AbortSignal, b?: AbortSignal | null): AbortSignal {
  if (!b) return a;
  const controller = new AbortController();
  const abort = () => controller.abort();
  a.addEventListener("abort", abort, { once: true });
  b.addEventListener("abort", abort, { once: true });
  if (a.aborted || b.aborted) controller.abort();
  return controller.signal;
}

// ── Streaming transport (SSE) ───────────────────────────────────────────────

/** Messages sent by the Rust `proxy_stream` command over its IPC channel. */
type ProxyStreamEvent =
  | { kind: "init"; status: number; headers: Record<string, string> }
  | { kind: "chunk"; data: string }
  | { kind: "end" }
  | { kind: "error"; message: string };

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/**
 * Like `localFetch`, but the returned `Response` body streams incrementally
 * in both web and Tauri builds. Use for SSE endpoints (agent runs, audits)
 * where waiting for the full body would defeat the point of streaming.
 *
 * In Tauri, chunks arrive over a Tauri IPC channel from `proxy_stream`;
 * aborting `options.signal` cancels the Rust task via `proxy_stream_cancel`.
 */
export async function localFetchStream(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  if (!isTauri()) return localFetch(path, options);

  const url = `${backendOrigin()}${path}`;
  const method = (options?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string> || {}),
  };
  const body = options?.body
    ? typeof options.body === "string"
      ? options.body
      : JSON.stringify(options.body)
    : undefined;

  const streamId = crypto.randomUUID();
  // Held in an object so TypeScript doesn't narrow the assignment inside
  // `start()` away (closures aren't tracked by control-flow analysis).
  const ctrl: { current: ReadableStreamDefaultController<Uint8Array> | null } = { current: null };
  const stream = new ReadableStream<Uint8Array>({
    start(c) { ctrl.current = c; },
    cancel() {
      invoke("proxy_stream_cancel", { streamId }).catch(() => { /* finished */ });
    },
  });

  let settleInit!: (v: { status: number; headers: Record<string, string> }) => void;
  let failInit!: (e: unknown) => void;
  const initPromise = new Promise<{ status: number; headers: Record<string, string> }>(
    (resolve, reject) => { settleInit = resolve; failInit = reject; },
  );
  let initDone = false;

  const channel = new Channel<ProxyStreamEvent>();
  channel.onmessage = (ev) => {
    switch (ev.kind) {
      case "init":
        initDone = true;
        settleInit({ status: ev.status, headers: ev.headers });
        break;
      case "chunk":
        ctrl.current?.enqueue(base64ToBytes(ev.data));
        break;
      case "end":
        ctrl.current?.close();
        break;
      case "error": {
        const err = new Error(ev.message);
        if (!initDone) { initDone = true; failInit(err); }
        try { ctrl.current?.error(err); } catch { /* already closed */ }
        break;
      }
    }
  };

  const onAbort = () => {
    invoke("proxy_stream_cancel", { streamId }).catch(() => { /* finished */ });
    const err = new DOMException("Aborted", "AbortError");
    if (!initDone) { initDone = true; failInit(err); }
    try { ctrl.current?.error(err); } catch { /* already closed */ }
  };
  if (options?.signal) {
    if (options.signal.aborted) onAbort();
    else options.signal.addEventListener("abort", onAbort, { once: true });
  }

  try {
    await invoke("proxy_stream", {
      request: { url, method, headers, body },
      streamId,
      onEvent: channel,
    });
  } catch (e) {
    if (!initDone) { initDone = true; failInit(e); }
    try { ctrl.current?.error(e); } catch { /* already closed */ }
  }

  const init = await initPromise;
  return new Response(stream, { status: init.status, headers: init.headers });
}

export function createWebSocket(path: string): WebSocket {
  const wsUrl = `ws://localhost:8001${path}`;
  return new WebSocket(wsUrl);
}

/**
 * Absolute origin for engine URLs that are shared/opened outside the app
 * (report links, share links). Web dev proxies through the Vite origin;
 * Tauri and built web builds talk to the engine directly on 127.0.0.1.
 */
export function engineOrigin(): string {
  if (isTauri()) return "http://localhost:8001";
  return import.meta.env.DEV ? window.location.origin : "http://localhost:8001";
}

// ── Knowledge graph ─────────────────────────────────────────────────────────

export interface KnowledgeConnection {
  direction: "incoming" | "outgoing";
  entity: string;
  entity_type: string;
  relation: string;
  evidence: string;
}

export interface KnowledgeEntityDetail {
  entity: {
    id: string;
    name: string;
    type: string;
    occurrences: number;
  };
  connections: KnowledgeConnection[];
  connection_count: number;
}

export interface KnowledgeGraphData {
  nodes: Array<{ id: string; label: string; group: string; val?: number }>;
  links: Array<{ source: string; target: string; label?: string }>;
}

export interface KnowledgeStats {
  entity_count: number;
  relation_count: number;
  [key: string]: unknown;
}

export async function fetchKnowledgeEntity(
  entityId: string,
): Promise<KnowledgeEntityDetail> {
  const res = await localFetch(`/knowledge/entity/${encodeURIComponent(entityId)}`);
  if (!res.ok) {
    if (res.status === 404) {
      throw new Error(`Entity not found: ${entityId}`);
    }
    throw new Error(`Failed to fetch entity (${res.status})`);
  }
  return res.json();
}

export async function fetchKnowledgeGraph(
  maxNodes = 100,
): Promise<KnowledgeGraphData> {
  const res = await localFetch(`/knowledge/graph?max_nodes=${maxNodes}`);
  if (!res.ok) throw new Error(`Failed to fetch graph (${res.status})`);
  return res.json();
}

export async function searchKnowledge(
  query: string,
  limit = 20,
): Promise<{ results: unknown[] }> {
  const res = await localFetch(
    `/knowledge/search?query=${encodeURIComponent(query)}&limit=${limit}`,
  );
  if (!res.ok) throw new Error(`Search failed (${res.status})`);
  return res.json();
}

export async function fetchKnowledgeStats(): Promise<KnowledgeStats> {
  const res = await localFetch("/knowledge/stats");
  if (!res.ok) throw new Error(`Stats failed (${res.status})`);
  return res.json();
}

// ── Missions ────────────────────────────────────────────────────────────────

export interface MissionResult {
  id: number;
  run_at: number;
  result_text: string;
  success: boolean;
}

export interface MissionResultsResponse {
  mission_id: string;
  results: MissionResult[];
  count: number;
}

export async function fetchMissionResults(
  missionId: string,
  limit = 50,
): Promise<MissionResultsResponse> {
  const res = await localFetch(
    `/mission/${encodeURIComponent(missionId)}/results?limit=${limit}`,
  );
  if (res.status === 404) {
    throw new Error(`Mission not found: ${missionId}`);
  }
  if (!res.ok) {
    throw new Error(`Failed to fetch mission results (${res.status})`);
  }
  return res.json();
}

// ── MoA preset configuration ────────────────────────────────────────────────

export interface MoaPreset {
  reference_models?: Array<{ provider: string; model?: string }>;
  aggregator: { provider: string; model?: string };
  reference_temperature?: number;
  aggregator_temperature?: number;
  max_tokens?: number;
  enabled?: boolean;
}

export interface MoaPresetsResponse {
  presets: Record<string, MoaPreset>;
  has_override: boolean;
}

export async function fetchMoaPresets(): Promise<MoaPresetsResponse> {
  const res = await localFetch("/v2/llm/moa/presets");
  if (!res.ok) throw new Error(`Failed to fetch MoA presets (${res.status})`);
  return res.json();
}

export async function saveMoaPresets(presets: Record<string, MoaPreset>): Promise<{ status: string; preset_count: number }> {
  const res = await localFetch("/v2/llm/moa/presets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ presets }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Failed to save MoA presets (${res.status}): ${detail}`);
  }
  return res.json();
}

export async function clearMoaPresets(): Promise<{ status: string }> {
  const res = await localFetch("/v2/llm/moa/presets", { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to clear MoA presets (${res.status})`);
  return res.json();
}
