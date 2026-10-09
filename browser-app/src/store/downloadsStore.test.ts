import { describe, it, expect, beforeEach } from "vitest";
import {
  useDownloadsStore, refreshDownloads, refreshTransfers, openDownload, removeDownload,
  startHttpDownload, pauseHttpDownload, addTorrent,
} from "./downloadsStore";

beforeEach(() => {
  useDownloadsStore.setState({
    downloads: [],
    transfers: [],
    torrents: [],
    loading: false,
    error: null,
    expanded: false,
    rev: 0,
  });
  // Wipe the Tauri flag so the helpers short-circuit in the jsdom test env.
  (globalThis as unknown as { __TAURI__?: unknown }).__TAURI__ = undefined;
});

describe("downloadsStore - setters", () => {
  it("setDownloads replaces the list and clears any prior error", () => {
    useDownloadsStore.setState({ error: "old error" });
    useDownloadsStore.getState().setDownloads([
      { filename: "a.pdf", path: "/x/a.pdf", size_bytes: 100, modified_at: 1, state: "complete" },
    ]);
    const s = useDownloadsStore.getState();
    expect(s.downloads).toHaveLength(1);
    expect(s.downloads[0].filename).toBe("a.pdf");
    expect(s.error).toBeNull();
  });

  it("setLoading toggles the loading flag", () => {
    expect(useDownloadsStore.getState().loading).toBe(false);
    useDownloadsStore.getState().setLoading(true);
    expect(useDownloadsStore.getState().loading).toBe(true);
    useDownloadsStore.getState().setLoading(false);
    expect(useDownloadsStore.getState().loading).toBe(false);
  });

  it("setExpanded toggles the expanded panel flag", () => {
    expect(useDownloadsStore.getState().expanded).toBe(false);
    useDownloadsStore.getState().setExpanded(true);
    expect(useDownloadsStore.getState().expanded).toBe(true);
  });

  it("bumpRev increments the rev counter", () => {
    const before = useDownloadsStore.getState().rev;
    useDownloadsStore.getState().bumpRev();
    useDownloadsStore.getState().bumpRev();
    expect(useDownloadsStore.getState().rev).toBe(before + 2);
  });
});

describe("downloadsStore - Tauri helpers in non-Tauri env", () => {
  it("refreshDownloads is a no-op when __TAURI__ is not present", async () => {
    await refreshDownloads();
    const s = useDownloadsStore.getState();
    expect(s.downloads).toEqual([]);
    expect(s.loading).toBe(false);
    expect(s.error).toBeNull();
  });

  it("refreshTransfers is a no-op when __TAURI__ is not present", async () => {
    await refreshTransfers();
    const s = useDownloadsStore.getState();
    expect(s.transfers).toEqual([]);
    expect(s.torrents).toEqual([]);
  });

  it("startHttpDownload returns null outside Tauri", async () => {
    expect(await startHttpDownload("https://example.com/f.bin", 4)).toBeNull();
  });

  it("pauseHttpDownload is a no-op outside Tauri", async () => {
    await pauseHttpDownload("missing");
    expect(useDownloadsStore.getState().transfers).toEqual([]);
  });

  it("addTorrent returns null outside Tauri", async () => {
    expect(await addTorrent("magnet:?xt=urn:btih:abc")).toBeNull();
  });

  it("openDownload is a no-op when __TAURI__ is not present", async () => {
    // Should not throw.
    await openDownload("/x/a.pdf");
    expect(useDownloadsStore.getState().downloads).toEqual([]);
  });

  it("removeDownload is a no-op when __TAURI__ is not present", async () => {
    await removeDownload("/x/a.pdf");
    expect(useDownloadsStore.getState().downloads).toEqual([]);
  });
});

describe("downloadsStore - Tauri helpers with stubbed Tauri", () => {
  it("refreshDownloads populates the list and clears loading on success", async () => {
    const fakeList = [
      { filename: "a.pdf", path: "/x/a.pdf", size_bytes: 100, modified_at: 1, state: "complete" },
    ];
    (globalThis as unknown as { __TAURI__: unknown }).__TAURI__ = {
      core: { invoke: async (cmd: string) => (cmd === "browser_list_downloads" ? fakeList : null) },
    };
    await refreshDownloads();
    const s = useDownloadsStore.getState();
    expect(s.downloads).toEqual(fakeList);
    expect(s.loading).toBe(false);
    expect(s.error).toBeNull();
  });

  it("refreshDownloads stores an error message on failure", async () => {
    (globalThis as unknown as { __TAURI__: unknown }).__TAURI__ = {
      core: { invoke: async () => { throw new Error("boom"); } },
    };
    await refreshDownloads();
    const s = useDownloadsStore.getState();
    expect(s.error).toContain("boom");
    expect(s.loading).toBe(false);
  });

  it("removeDownload invokes the command and triggers a refresh", async () => {
    const calls: string[] = [];
    (globalThis as unknown as { __TAURI__: unknown }).__TAURI__ = {
      core: { invoke: async (cmd: string) => { calls.push(cmd); return null; } },
    };
    await removeDownload("/x/a.pdf");
    expect(calls).toContain("browser_remove_download");
    expect(calls).toContain("browser_list_downloads");
  });

  it("refreshTransfers populates http + torrent lists", async () => {
    const httpRow = {
      id: "h1", kind: "http", name: "f.bin", url: "https://example.com/f.bin",
      path: "/x/f.bin", total_bytes: 100, total_known: true, downloaded_bytes: 10,
      progress: 0.1, speed_bps: 5, eta_secs: 18, status: "downloading",
      connections: 4, error: null, modified_at: 1,
    };
    const torrentRow = {
      id: "t1", kind: "torrent", name: "ubuntu", info_hash: "t1",
      magnet: "magnet:?xt=urn:btih:t1", path: "/x", total_bytes: 200,
      total_known: true, downloaded_bytes: 20, progress: 0.1, speed_bps: 5,
      eta_secs: 36, status: "downloading", peers: 3, error: null, modified_at: 1,
    };
    (globalThis as unknown as { __TAURI__: unknown }).__TAURI__ = {
      core: {
        invoke: async (cmd: string) => {
          if (cmd === "browser_http_list") return [httpRow];
          if (cmd === "browser_torrent_list") return [torrentRow];
          return null;
        },
      },
    };
    await refreshTransfers();
    const s = useDownloadsStore.getState();
    expect(s.transfers).toHaveLength(1);
    expect(s.transfers[0].id).toBe("h1");
    expect(s.torrents).toHaveLength(1);
    expect(s.torrents[0].peers).toBe(3);
  });

  it("setTransfers / setTorrents replace managed rows", () => {
    useDownloadsStore.getState().setTransfers([]);
    useDownloadsStore.getState().setTorrents([]);
    expect(useDownloadsStore.getState().transfers).toEqual([]);
    expect(useDownloadsStore.getState().torrents).toEqual([]);
  });
});
