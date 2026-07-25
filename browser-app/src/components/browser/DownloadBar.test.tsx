import { describe, it, expect } from "vitest";
import { displayName, stateLabel } from "./DownloadBar";
import type { Download } from "../../store/downloadsStore";

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
