import { create } from "zustand";

export type DownloadState = "in_progress" | "complete" | "empty";

export interface Download {
  filename: string;
  path: string;
  size_bytes: number;
  modified_at: number;
  state: DownloadState;
}

interface DownloadsState {
  downloads: Download[];
  loading: boolean;
  error: string | null;
  expanded: boolean;
  /** Monotonically increasing counter; bump to force a UI refresh after
   *  triggering a remove / open from outside this store. */
  rev: number;

  setDownloads: (d: Download[]) => void;
  setLoading: (l: boolean) => void;
  setError: (e: string | null) => void;
  setExpanded: (e: boolean) => void;
  bumpRev: () => void;
}

export const useDownloadsStore = create<DownloadsState>((set) => ({
  downloads: [],
  loading: false,
  error: null,
  expanded: false,
  rev: 0,

  setDownloads: (d) => set({ downloads: d, error: null }),
  setLoading: (l) => set({ loading: l }),
  setError: (e) => set({ error: e }),
  setExpanded: (e) => set({ expanded: e }),
  bumpRev: () => set((s) => ({ rev: s.rev + 1 })),
}));

function isTauriHost(): boolean {
  if (typeof window === "undefined") return false;
  const flag = (window as unknown as Record<string, unknown>).__TAURI__;
  // The key alone can be present on some embeds; the value must be a
  // non-null object with a .core.invoke function for us to be safe.
  return (
    typeof flag === "object" &&
    flag !== null &&
    typeof (flag as { core?: { invoke?: unknown } }).core?.invoke === "function"
  );
}

/** Refresh downloads by calling the Tauri command. No-op in the browser
 *  (non-Tauri) build. Errors are stored on the state rather than thrown
 *  so the UI can render a placeholder. */
export async function refreshDownloads(): Promise<void> {
  if (!isTauriHost()) return;
  const { setDownloads, setLoading, setError } = useDownloadsStore.getState();
  setLoading(true);
  try {
    const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
      core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    const list = await tauri.core.invoke("browser_list_downloads") as Download[];
    setDownloads(list);
  } catch (e) {
    setError(String(e));
  } finally {
    setLoading(false);
  }
}

export async function openDownload(path: string): Promise<void> {
  if (!isTauriHost()) return;
  const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
    core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  };
  try {
    await tauri.core.invoke("browser_open_download", { path });
  } catch {
    // Surface as a generic error on the next refresh.
  }
}

export async function removeDownload(path: string): Promise<void> {
  if (!isTauriHost()) return;
  const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
    core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  };
  try {
    await tauri.core.invoke("browser_remove_download", { path });
  } finally {
    await refreshDownloads();
  }
}
