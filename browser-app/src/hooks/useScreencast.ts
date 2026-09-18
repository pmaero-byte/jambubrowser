/**
 * Live-view screencast hook.
 *
 * Streams JPEG frames from the Rust `browser_start_screencast` command (CDP
 * `Page.startScreencast`) at up to ~30–60 FPS — replacing screenshot polling.
 * Falls back silently when not running under Tauri or when the stream errors,
 * so the caller can keep its poller as a safety net.
 */
import { useEffect, useRef, useState } from "react";

export type ScreencastEvent =
  | { kind: "frame"; data: string }
  | { kind: "error"; message: string }
  | { kind: "end" };

export interface UseScreencastOptions {
  enabled?: boolean;
  quality?: number;
  maxWidth?: number;
}

export interface UseScreencastResult {
  /** Latest frame as a data URL, or null until the first frame arrives. */
  frame: string | null;
  /** Set when the stream could not start (non-Tauri is not an error). */
  error: string | null;
}

export function useScreencast(
  tabId: string | undefined,
  options: UseScreencastOptions = {},
): UseScreencastResult {
  const { enabled = true, quality = 70, maxWidth = 1280 } = options;
  const [frame, setFrame] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const frameRef = useRef<string | null>(null);

  useEffect(() => {
    frameRef.current = null;
    setFrame(null);
    setError(null);

    if (!tabId || !enabled) return;
    const isTauri = typeof window !== "undefined" && "__TAURI__" in window;
    if (!isTauri) return;

    let active = true;
    let stop: (() => void) | null = null;

    (async () => {
      try {
        const { invoke, Channel } = await import("@tauri-apps/api/core");
        const channel = new Channel<ScreencastEvent>();
        channel.onmessage = (message) => {
          if (!active) return;
          if (message.kind === "frame") {
            frameRef.current = `data:image/jpeg;base64,${message.data}`;
            setFrame(frameRef.current);
          } else if (message.kind === "error") {
            setError(message.message);
          }
        };
        await invoke("browser_start_screencast", {
          tabId, quality, maxWidth, onFrame: channel,
        });
        stop = () => {
          invoke("browser_stop_screencast", { tabId }).catch(() => { /* ended */ });
        };
      } catch (e) {
        if (active) setError(String(e));
      }
    })();

    return () => {
      active = false;
      stop?.();
    };
  }, [tabId, enabled, quality, maxWidth]);

  return { frame, error };
}
