const BACKEND_URL = "http://localhost:8001";

/**
 * Local fetch helper that routes requests to the local backend
 * In production, this would use Tauri's invoke or IPC
 */
export async function localFetch(path: string, options?: RequestInit): Promise<Response> {
  const url = `${BACKEND_URL}${path}`;
  return fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
}

/**
 * WebSocket connection for live updates
 */
export function createWebSocket(path: string): WebSocket {
  const wsUrl = `ws://localhost:8001${path}`;
  return new WebSocket(wsUrl);
}
