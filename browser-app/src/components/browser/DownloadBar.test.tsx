import { describe, it, expect } from "vitest";
import { displayName, stateLabel, transferLabel, formatBytes, formatSpeed, formatEta } from "./DownloadBar";
import type { Download, HttpTransfer, TorrentTransfer } from "../../store/downloadsStore";

function dl(over: Partial<Download>): Download {
  return {
    filename: "file.pdf",
    path: "/tmp/file.pdf",
    size_bytes: 0,
    modified_at: 0,
    state: "complete",
    ...over,
  };
}

function http(over: Partial<HttpTransfer>): HttpTransfer {
  return {
    id: "h1",
    kind: "http",
    name: "big.iso",
    url: "https://example.com/big.iso",
    path: "/tmp/big.iso",
    total_bytes: 1000,
    total_known: true,
    downloaded_bytes: 250,
    progress: 0.25,
    speed_bps: 100,
    eta_secs: 7,
    status: "downloading",
    connections: 8,
    error: null,
    modified_at: 0,
    ...over,
  };
}

function torrent(over: Partial<TorrentTransfer>): TorrentTransfer {
  return {
    id: "abc",
    kind: "torrent",
    name: "ubuntu",
    info_hash: "abc",
    magnet: "magnet:?xt=urn:btih:abc",
    path: "/tmp/torrents",
    total_bytes: 2000,
    total_known: true,
    downloaded_bytes: 2000,
    progress: 1,
    speed_bps: 0,
    eta_secs: 0,
    status: "complete",
    peers: 12,
    error: null,
    modified_at: 0,
    ...over,
  };
}

describe("displayName", () => {
  it("strips the .crdownload suffix from in-progress downloads", () => {
    expect(displayName(dl({ filename: "big.pdf.crdownload", state: "in_progress" }))).toBe("big.pdf");
  });

  it("leaves complete filenames untouched", () => {
    expect(displayName(dl({ filename: "report.pdf", state: "complete" }))).toBe("report.pdf");
  });

  it("leaves stray .crdownload files alone when not marked in_progress", () => {
    // Defensive: the suffix is only stripped when the state agrees.
    expect(displayName(dl({ filename: "x.crdownload", state: "complete" }))).toBe("x.crdownload");
  });
});

describe("stateLabel", () => {
  it("shows bytes received for in-progress downloads", () => {
    const label = stateLabel(dl({ state: "in_progress", filename: "a.bin.crdownload", size_bytes: 2048 }));
    expect(label.text).toBe("Downloading… 2.0 KB");
    expect(label.className).toContain("amber");
  });

  it("shows the final size for complete downloads", () => {
    expect(stateLabel(dl({ state: "complete", size_bytes: 5 * 1024 * 1024 })).text).toBe("5.0 MB");
  });

  it("labels zero-byte files as empty", () => {
    expect(stateLabel(dl({ state: "empty", size_bytes: 0 })).text).toBe("Empty");
  });
});

describe("format helpers", () => {
  it("formats bytes across units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("formats stalled speed", () => {
    expect(formatSpeed(0)).toBe("stalled");
    expect(formatSpeed(2048)).toContain("/s");
  });

  it("formats ETA", () => {
    expect(formatEta(1000, 1000, 100)).toBe("");
    expect(formatEta(1000, 0, 0)).toBe("· ETA —");
    expect(formatEta(1000, 500, 100)).toContain("left");
  });
});

describe("transferLabel", () => {
  it("shows % + sizes + speed for active HTTP", () => {
    const label = transferLabel(http({}));
    expect(label).toContain("25%");
    expect(label).toContain("/s");
  });

  it("shows paused state with bytes", () => {
    expect(transferLabel(http({ status: "paused" }))).toContain("Paused");
  });

  it("shows complete with total", () => {
    expect(transferLabel(http({ status: "complete", downloaded_bytes: 1000 }))).toContain("Complete");
  });

  it("shows torrent error text", () => {
    expect(transferLabel(torrent({ status: "error", error: "tracker fail" }))).toContain("tracker fail");
  });

  it("handles unknown total without crashing", () => {
    const label = transferLabel(http({ total_known: false, total_bytes: 0 }));
    expect(label).toContain("/s");
  });
});
