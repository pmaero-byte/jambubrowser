// Unified API transport — works in web builds and inside the Tauri WebView.
// In Tauri, HTTP is routed through the Rust `proxy_localhost` command so the
// WebView CSP (which blocks arbitrary connect-src) stays tight.

import { invoke } from "@tauri-apps/api/core";

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
 */
export async function localFetch(path: string, options?: RequestInit): Promise<Response> {
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
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
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

export function createWebSocket(path: string): WebSocket {
  const wsUrl = `ws://localhost:8001${path}`;
  return new WebSocket(wsUrl);
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
