const BACKEND_URL = import.meta.env.DEV ? "" : "http://localhost:8001";

const DEFAULT_TIMEOUT_MS = 30000;

export async function localFetch(path: string, options?: RequestInit): Promise<Response> {
  const url = `${BACKEND_URL}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(options?.headers as Record<string, string> || {}),
      },
    });
  } finally {
    clearTimeout(timer);
  }
}

export function createWebSocket(path: string): WebSocket {
  const wsUrl = import.meta.env.DEV
    ? `ws://localhost:8001${path}`
    : `ws://localhost:8001${path}`;
  return new WebSocket(wsUrl);
}
