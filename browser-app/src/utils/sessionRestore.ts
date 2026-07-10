import { useEffect } from "react";
import { useAppStore, BrowserTab } from "../store/appStore";

const SESSION_KEY = "jambu-browser-session";
const SESSION_VERSION = 1;
const SAVE_DEBOUNCE_MS = 500;

export interface SavedSession {
  version: number;
  tabs: BrowserTab[];
  activeTabId: string;
}

export function loadSession(): SavedSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SavedSession>;
    if (!parsed || parsed.version !== SESSION_VERSION) return null;
    if (!Array.isArray(parsed.tabs) || typeof parsed.activeTabId !== "string") return null;
    return {
      version: SESSION_VERSION,
      tabs: parsed.tabs as BrowserTab[],
      activeTabId: parsed.activeTabId,
    };
  } catch {
    // Corrupted JSON, quota error, or localStorage disabled — fall back to defaults.
    return null;
  }
}

export function saveSession(tabs: BrowserTab[], activeTabId: string): void {
  try {
    const payload: SavedSession = { version: SESSION_VERSION, tabs, activeTabId };
    localStorage.setItem(SESSION_KEY, JSON.stringify(payload));
  } catch {
    // Quota exceeded or storage disabled — silently drop the save. Better to
    // lose the session than to crash the app.
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    /* ignore */
  }
}

// Mount-once hook: reads the saved session and dispatches restoreSession, then
// debounces subsequent state changes back to localStorage.
export function useSessionRestore(): void {
  const browserTabs = useAppStore((s) => s.browserTabs);
  const activeBrowserTabId = useAppStore((s) => s.activeBrowserTabId);
  const restoreSession = useAppStore((s) => s.restoreSession);

  useEffect(() => {
    const saved = loadSession();
    if (saved) restoreSession(saved.tabs, saved.activeTabId);
  }, [restoreSession]);

  useEffect(() => {
    const id = setTimeout(() => saveSession(browserTabs, activeBrowserTabId), SAVE_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [browserTabs, activeBrowserTabId]);
}
