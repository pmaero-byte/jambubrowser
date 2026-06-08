const BACKEND_URL = import.meta.env.DEV ? "" : "http://localhost:8001";

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

export function createWebSocket(path: string): WebSocket {
  const wsUrl = import.meta.env.DEV
    ? `ws://localhost:5173${path}`
    : `ws://localhost:8001${path}`;
  return new WebSocket(wsUrl);
}
