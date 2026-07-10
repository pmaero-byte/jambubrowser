import { create } from "zustand";

export interface BrowsingHistoryEntry {
  url: string;
  title: string;
  visitedAt: number;
}

const STORAGE_KEY = "jambu-browser-history";
const MAX_ENTRIES = 500;

function loadFromStorage(): BrowsingHistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e) => e && typeof e.url === "string" && typeof e.title === "string" && typeof e.visitedAt === "number"
    );
  } catch {
    return [];
  }
}

function saveToStorage(entries: BrowsingHistoryEntry[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Quota exceeded — drop the oldest entry and retry once.
    if (entries.length > 1) {
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, entries.length - 1)));
      } catch {
        // Give up; the user can clear history manually.
      }
    }
  }
}

interface BrowsingHistoryState {
  entries: BrowsingHistoryEntry[];
  addEntry: (url: string, title: string) => void;
  clearAll: () => void;
  removeEntry: (url: string, visitedAt: number) => void;
}

export const useBrowsingHistoryStore = create<BrowsingHistoryState>((set, get) => ({
  entries: loadFromStorage(),

  addEntry: (url, title) => {
    if (!url || url === "about:blank") return;
    const entry: BrowsingHistoryEntry = { url, title, visitedAt: Date.now() };
    const next = [
      entry,
      // Drop any prior entry for the same URL so the latest visit floats
      // to the top without leaving stale duplicates behind.
      ...get().entries.filter((e) => e.url !== url),
    ].slice(0, MAX_ENTRIES);
    saveToStorage(next);
    set({ entries: next });
  },

  clearAll: () => {
    saveToStorage([]);
    set({ entries: [] });
  },

  removeEntry: (url, visitedAt) => {
    const next = get().entries.filter((e) => !(e.url === url && e.visitedAt === visitedAt));
    saveToStorage(next);
    set({ entries: next });
  },
}));
