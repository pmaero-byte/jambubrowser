import { create } from "zustand";

export type DownloadState = "in_progress" | "complete" | "empty";

export interface Download {
  filename: string;
  path: string;
  size_bytes: number;
  modified_at: number;
  state: DownloadState;
}

// ── Managed transfers (segmented HTTP + torrents) ──────────────────
// Mirrors the Rust serde shapes in
// `src-tauri/src/chromium/download_manager.rs` and `torrent.rs`.

export type TransferStatus =
  | "queued"
  | "downloading"
  | "paused"
  | "complete"
  | "error"
  | "cancelled";

export type TransferKind = "http" | "torrent" | "chrome";

export interface HttpTransfer {
  id: string;
  kind: TransferKind;
  name: string;
  url: string;
  path: string;
  total_bytes: number;
  total_known: boolean;
  downloaded_bytes: number;
  progress: number; // 0–1
  speed_bps: number;
  eta_secs: number | null;
  status: TransferStatus;
  connections: number;
  error: string | null;
  modified_at: number;
}

export interface TorrentTransfer {
  id: string;
  kind: TransferKind;
  name: string;
  info_hash: string;
  magnet: string;
  path: string;
  total_bytes: number;
  total_known: boolean;
  downloaded_bytes: number;
  progress: number;
  speed_bps: number;
  eta_secs: number | null;
  status: TransferStatus;
  peers: number;
  error: string | null;
  modified_at: number;
}

interface DownloadsState {
  downloads: Download[];
  transfers: HttpTransfer[];
  torrents: TorrentTransfer[];
  loading: boolean;
  error: string | null;
  expanded: boolean;
  /** Monotonically increasing counter; bump to force a UI refresh after
   *  triggering a remove / open from outside this store. */
  rev: number;

  setDownloads: (d: Download[]) => void;
  setTransfers: (t: HttpTransfer[]) => void;
  setTorrents: (t: TorrentTransfer[]) => void;
  setLoading: (l: boolean) => void;
  setError: (e: string | null) => void;
  setExpanded: (e: boolean) => void;
  bumpRev: () => void;
}

export const useDownloadsStore = create<DownloadsState>((set) => ({
  downloads: [],
  transfers: [],
  torrents: [],
  loading: false,
  error: null,
  expanded: false,
  rev: 0,

  setDownloads: (d) => set({ downloads: d, error: null }),
  setTransfers: (t) => set({ transfers: t }),
  setTorrents: (t) => set({ torrents: t }),
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

function tauriInvoke(): (cmd: string, args?: Record<string, unknown>) => Promise<unknown> {
  const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
    core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  };
  return (cmd, args) => tauri.core.invoke(cmd, args);
}

/** Refresh downloads by calling the Tauri command. No-op in the browser
 *  (non-Tauri) build. Errors are stored on the state rather than thrown
 *  so the UI can render a placeholder. */
export async function refreshDownloads(): Promise<void> {
  if (!isTauriHost()) return;
  const { setDownloads, setLoading, setError } = useDownloadsStore.getState();
  setLoading(true);
  try {
    const invoke = tauriInvoke();
    const list = await invoke("browser_list_downloads") as Download[];
    setDownloads(list);
  } catch (e) {
    setError(String(e));
  } finally {
    setLoading(false);
  }
}

/** Refresh segmented-HTTP + torrent rows. No-op outside Tauri. */
export async function refreshTransfers(): Promise<void> {
  if (!isTauriHost()) return;
  const { setTransfers, setTorrents, setError } = useDownloadsStore.getState();
  try {
    const invoke = tauriInvoke();
    const [http, torrents] = await Promise.all([
      invoke("browser_http_list").catch(() => [] as HttpTransfer[]),
      invoke("browser_torrent_list").catch(() => [] as TorrentTransfer[]),
    ]);
    setTransfers(http as HttpTransfer[]);
    setTorrents(torrents as TorrentTransfer[]);
  } catch (e) {
    setError(String(e));
  }
}

/** Refresh everything (Chrome files + managed transfers). */
export async function refreshAllDownloads(): Promise<void> {
  await Promise.all([refreshDownloads(), refreshTransfers()]);
}

export async function openDownload(path: string): Promise<void> {
  if (!isTauriHost()) return;
  const invoke = tauriInvoke();
  try {
    await invoke("browser_open_download", { path });
  } catch {
    // Surface as a generic error on the next refresh.
  }
}

export async function removeDownload(path: string): Promise<void> {
  if (!isTauriHost()) return;
  const invoke = tauriInvoke();
  try {
    await invoke("browser_remove_download", { path });
  } finally {
    await refreshDownloads();
  }
}

// ── Segmented HTTP ───────────────────────────────────────────────

export async function startHttpDownload(url: string, connections = 8): Promise<HttpTransfer | null> {
  if (!isTauriHost()) return null;
  const invoke = tauriInvoke();
  const t = await invoke("browser_http_start", { url, connections }) as HttpTransfer;
  useDownloadsStore.getState().bumpRev();
  await refreshTransfers();
  return t;
}

export async function pauseHttpDownload(id: string): Promise<void> {
  if (!isTauriHost()) return;
  await tauriInvoke()("browser_http_pause", { id });
  await refreshTransfers();
}

export async function resumeHttpDownload(id: string): Promise<void> {
  if (!isTauriHost()) return;
  await tauriInvoke()("browser_http_resume", { id });
  await refreshTransfers();
}

export async function cancelHttpDownload(id: string): Promise<void> {
  if (!isTauriHost()) return;
  await tauriInvoke()("browser_http_cancel", { id });
  await refreshTransfers();
}

export async function removeHttpDownload(id: string, deleteFile = false): Promise<void> {
  if (!isTauriHost()) return;
  await tauriInvoke()("browser_http_remove", { id, delete_file: deleteFile });
  await refreshTransfers();
}

// ── Torrents ─────────────────────────────────────────────────────

export async function addTorrent(input: string): Promise<TorrentTransfer | null> {
  if (!isTauriHost()) return null;
  const t = await tauriInvoke()("browser_torrent_add", { input, only_files: null }) as TorrentTransfer;
  useDownloadsStore.getState().bumpRev();
  await refreshTransfers();
  return t;
}

/** Upload a `.torrent` file picked in the browser (base64-encoded bytes). */
export async function addTorrentFile(base64Bytes: string): Promise<TorrentTransfer | null> {
  if (!isTauriHost()) return null;
  const t = await tauriInvoke()("browser_torrent_add_bytes", {
    base64_bytes: base64Bytes,
  }) as TorrentTransfer;
  useDownloadsStore.getState().bumpRev();
  await refreshTransfers();
  return t;
}

export async function pauseTorrent(id: string): Promise<void> {
  if (!isTauriHost()) return;
  await tauriInvoke()("browser_torrent_pause", { id });
  await refreshTransfers();
}

export async function resumeTorrent(id: string): Promise<void> {
  if (!isTauriHost()) return;
  await tauriInvoke()("browser_torrent_resume", { id });
  await refreshTransfers();
}

export async function removeTorrent(id: string, deleteFiles = false): Promise<void> {
  if (!isTauriHost()) return;
  await tauriInvoke()("browser_torrent_remove", { id, delete_files: deleteFiles });
  await refreshTransfers();
}
