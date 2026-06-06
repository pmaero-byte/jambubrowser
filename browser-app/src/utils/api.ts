import { invoke } from '@tauri-apps/api/core';

export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI__' in window;
}

export async function localFetch(url: string, method: 'GET' | 'POST', body?: any): Promise<any> {
  if (isTauri()) {
    // Route requests through the Rust proxy to bypass CORS
    return await invoke('proxy_localhost', { url, method, body });
  } else {
    // Fall back to standard browser fetch during development
    const response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    });
    return await response.json();
  }
}
