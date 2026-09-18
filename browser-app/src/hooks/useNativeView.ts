/**
 * Native tab view hook (dual-mode tabs).
 *
 * When enabled, mounts the OS system webview as a child over the given
 * container via the Rust `browser_native_view` command and keeps it sized to
 * the container with a ResizeObserver. Polls the child URL for address-bar
 * sync. This is a *different engine* from the CDP stream view: no audits,
 * no fingerprint scripts — the pane must say so.
 *
 * Outside Tauri (or on error) the hook is inert and the caller falls back
 * to the stream view.
 */
import { useEffect, useState } from "react";
import type { RefObject } from "react";

export interface UseNativeViewResult {
  /** Current child URL (address-bar sync), or null when inactive. */
  liveUrl: string | null;
  /** Set when the native view could not start. */
  error: string | null;
}

const URL_POLL_MS = 2000;

type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

async function invokeNative(cmd: string, args: Record<string, unknown>): Promise<unknown> {
  const { invoke } = (await import("@tauri-apps/api/core")) as {
    invoke: InvokeFn;
  };
  return invoke(cmd, args);
}

async function readRect(
  containerRef: RefObject<HTMLElement | null>,
): Promise<{ x: number; y: number; width: number; height: number } | null> {
  const el = containerRef.current;
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return null;
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI__" in window;
}

export function useNativeView(
  tabId: string | undefined,
  url: string | undefined,
  containerRef: RefObject<HTMLElement | null>,
  enabled: boolean,
): UseNativeViewResult {
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Lifecycle: resize tracking, URL polling, and teardown on tab/disable.
  useEffect(() => {
    setLiveUrl(null);
    setError(null);
    if (!tabId || !enabled || !isTauri()) return;

    let active = true;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let observer: ResizeObserver | null = null;
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;

    const syncRect = async () => {
      const next = await readRect(containerRef);
      if (!active || !next || !tabId) return;
      try {
        await invokeNative("browser_native_set_rect", { tabId, ...next });
      } catch {
        /* child gone — teardown below handles it */
      }
    };
    if (typeof ResizeObserver !== "undefined" && containerRef.current) {
      observer = new ResizeObserver(() => {
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => void syncRect(), 150);
      });
      observer.observe(containerRef.current);
    }
    pollTimer = setInterval(async () => {
      if (!active || !tabId) return;
      try {
        const current = (await invokeNative("browser_native_url", { tabId })) as string;
        if (active && current) setLiveUrl(current);
      } catch {
        /* child gone */
      }
    }, URL_POLL_MS);

    return () => {
      active = false;
      if (pollTimer) clearInterval(pollTimer);
      if (resizeTimer) clearTimeout(resizeTimer);
      observer?.disconnect();
      if (tabId) {
        void invokeNative("browser_native_close", { tabId }).catch(() => {
          /* best effort */
        });
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabId, enabled]);

  // Navigation: (re)show the child at the URL, reusing it when alive.
  useEffect(() => {
    if (!tabId || !url || !enabled || !isTauri()) return;
    let cancelled = false;
    (async () => {
      try {
        const rect = await readRect(containerRef);
        if (cancelled || !rect) return;
        await invokeNative("browser_native_view", { tabId, url, ...rect });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabId, url, enabled]);

  return { liveUrl, error };
}
