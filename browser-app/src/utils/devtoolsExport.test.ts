import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  buildDevToolsSnapshot,
  exportDevToolsJSON,
  exportNetworkCSV,
  exportConsoleCSV,
  exportConsoleTXT,
  exportErrorsCSV,
  type DevtoolsState,
} from "./devtoolsExport";

/**
 * Capture the body of every Blob the export functions produce. We mock the
 * global Blob constructor so we can read its content synchronously (jsdom's
 * Blob polyfill is async-incompatible with our export functions which write
 * strings), and we replace the anchor's click() with a no-op so the test
 * runner stays silent.
 */
const capturedBlobs: { content: string; type: string }[] = [];
const originalBlob = globalThis.Blob;
const originalCreateObjectURL = URL.createObjectURL;
const originalRevokeObjectURL = URL.revokeObjectURL;

class CapturingBlob {
  type: string;
  private _content: string;
  constructor(parts: BlobPart[], options?: BlobPropertyBag) {
    this.type = options?.type ?? "";
    this._content = parts
      .map((p) => (typeof p === "string" ? p : new TextDecoder().decode(p as ArrayBuffer)))
      .join("");
  }
  get size(): number {
    return new TextEncoder().encode(this._content).byteLength;
  }
}

beforeEach(() => {
  capturedBlobs.length = 0;
  // Fake timers so the production setTimeout(() => URL.revokeObjectURL, 0)
  // cleanup fires against the mocked (no-op) revoke during the test instead
  // of leaking into teardown and calling the real revoke on a fake blob URL.
  vi.useFakeTimers();
  (globalThis as { Blob: unknown }).Blob = CapturingBlob as unknown as typeof Blob;
  URL.createObjectURL = vi.fn((blob: object) => {
    const cb = blob as { _content: string; type: string };
    capturedBlobs.push({ content: cb._content, type: cb.type });
    return "blob:mock-" + capturedBlobs.length;
  });
  URL.revokeObjectURL = vi.fn();
  // Prevent jsdom from actually following the download.
  const origCreate = document.createElement.bind(document);
  document.createElement = ((tag: string) => {
    const el = origCreate(tag);
    if (tag === "a") (el as HTMLAnchorElement).click = vi.fn();
    return el;
  }) as typeof document.createElement;
});

afterEach(() => {
  vi.useRealTimers();
  (globalThis as { Blob: unknown }).Blob = originalBlob;
  URL.createObjectURL = originalCreateObjectURL;
  URL.revokeObjectURL = originalRevokeObjectURL;
});

/** Run all pending timers so the production cleanup fires within the test. */
function flushCleanup(): void {
  vi.runAllTimers();
}

const minimalState: DevtoolsState = {
  devtoolsOpen: false,
  activeTab: "network",
  resources: [
    {
      name: "https://example.com/api/data",
      initiatorType: "fetch",
      startTime: 0,
      duration: 123,
      dnsStart: 0,
      dnsEnd: 1,
      connectStart: 1,
      connectEnd: 5,
      ttfb: 50,
      responseStart: 60,
      responseEnd: 123,
      transferSize: 4567,
      encodedBodySize: 4000,
      decodedBodySize: 4000,
      nextHopProtocol: "h2",
    },
  ],
  consoleEntries: [
    { level: "error", message: 'TypeError: cannot read "x" of undefined', timestamp: 1700000000000 },
    { level: "warn", message: "deprecated API", timestamp: 1700000001000 },
  ],
  errors: [
    { message: "Uncaught", filename: "app.js", lineno: 42, colno: 7, source: "js", timestamp: 1700000002000 },
  ],
  lcp: { renderTime: 1200, loadTime: 1200, size: 1024, id: "el-1", url: "https://example.com/" },
  fcp: { startTime: 800 },
  cls: { value: 0.05, sources: 1, hadRecentInput: false },
  longTasks: [{ duration: 60, startTime: 100, name: "self" }],
  navigation: {
    url: "https://example.com/page",
    domContentLoaded: 300,
    load: 500,
    domInteractive: 250,
    ttfb: 100,
    dnsTime: 10,
    tcpTime: 20,
    transferSize: 12345,
    decodedBodySize: 12000,
    type: "navigate",
  },
  perfSummary: {
    lcp: null,
    fcp: null,
    cls: null,
    navigation: null,
    totalResources: 0,
    totalTransferSize: 0,
    totalDuration: 0,
  },
  // Actions are no-ops for export tests.
  setDevtoolsOpen: () => {},
  setActiveTab: () => {},
  addResource: () => {},
  addConsoleEntry: () => {},
  addError: () => {},
  setLcp: () => {},
  setFcp: () => {},
  setCls: () => {},
  addLongTask: () => {},
  setNavigation: () => {},
  clearAll: () => {},
} as DevtoolsState;

/** Read the most recently captured blob's body. */
function lastBlobBody(): string {
  if (capturedBlobs.length === 0) {
    throw new Error("no blob was captured");
  }
  return capturedBlobs[capturedBlobs.length - 1].content;
}

describe("devtoolsExport", () => {
  describe("buildDevToolsSnapshot", () => {
    it("returns pretty-printed JSON containing every section", () => {
      const json = buildDevToolsSnapshot(minimalState);
      const parsed = JSON.parse(json);
      expect(parsed.navigation.url).toBe("https://example.com/page");
      expect(parsed.webVitals.lcp.renderTime).toBe(1200);
      expect(parsed.resources).toHaveLength(1);
      expect(parsed.consoleEntries).toHaveLength(2);
      expect(parsed.errors).toHaveLength(1);
      expect(parsed.longTasks).toHaveLength(1);
    });

    it("is valid JSON", () => {
      expect(() => JSON.parse(buildDevToolsSnapshot(minimalState))).not.toThrow();
    });
  });

  describe("exportDevToolsJSON", () => {
    it("downloads a JSON blob containing all sections", () => {
      exportDevToolsJSON(minimalState);
      flushCleanup();
      const body = lastBlobBody();
      expect(body).toContain('"navigation"');
      expect(body).toContain('"webVitals"');
      expect(body).toContain('"resources"');
      expect(body).toContain('"consoleEntries"');
      expect(body).toContain('"errors"');
      expect(body).toContain('"longTasks"');
      expect(body).toContain('"perfSummary"');
    });

    it("includes exportedAt timestamp", () => {
      exportDevToolsJSON(minimalState);
      flushCleanup();
      const parsed = JSON.parse(lastBlobBody());
      expect(parsed.exportedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    });

    it("includes counts object", () => {
      exportDevToolsJSON(minimalState);
      flushCleanup();
      const parsed = JSON.parse(lastBlobBody());
      expect(parsed.counts).toEqual({
        resources: 1,
        consoleEntries: 2,
        errors: 1,
        longTasks: 1,
      });
    });
  });

  describe("exportNetworkCSV", () => {
    it("emits a header row + one row per resource", () => {
      exportNetworkCSV(minimalState);
      flushCleanup();
      const lines = lastBlobBody().trim().split("\n");
      expect(lines[0]).toBe(
        "name,initiatorType,startTime,duration,dnsStart,dnsEnd,connectStart,connectEnd,ttfb,responseStart,responseEnd,transferSize,encodedBodySize,decodedBodySize,nextHopProtocol",
      );
      expect(lines).toHaveLength(2);
      expect(lines[1]).toContain("https://example.com/api/data");
      expect(lines[1]).toContain("fetch");
    });

    it("emits only a header for empty resources", () => {
      exportNetworkCSV({ ...minimalState, resources: [] });
      flushCleanup();
      const body = lastBlobBody().trim();
      expect(body.split("\n")).toHaveLength(1);
    });
  });

  describe("exportConsoleCSV", () => {
    it("emits timestamp, level, message columns", () => {
      exportConsoleCSV(minimalState);
      flushCleanup();
      const lines = lastBlobBody().trim().split("\n");
      expect(lines[0]).toBe("timestamp,level,message");
      expect(lines[1]).toContain("error");
      expect(lines[1]).toContain("TypeError");
      expect(lines[2]).toContain("warn");
    });
  });

  describe("exportConsoleTXT", () => {
    it("emits one bracketed line per console entry", () => {
      exportConsoleTXT(minimalState);
      flushCleanup();
      const lines = lastBlobBody().trim().split("\n");
      expect(lines).toHaveLength(2);
      // Each line must contain ISO timestamp + uppercase level + message.
      expect(lines[0]).toMatch(/^\[\d{4}-\d{2}-\d{2}T/);
      expect(lines[0]).toMatch(/ERROR/);
      expect(lines[0]).toContain("TypeError");
      expect(lines[1]).toMatch(/WARN/);
      expect(lines[1]).toContain("deprecated");
    });
  });

  describe("exportErrorsCSV", () => {
    it("emits timestamp, message, filename, lineno, colno, source columns", () => {
      exportErrorsCSV(minimalState);
      flushCleanup();
      const lines = lastBlobBody().trim().split("\n");
      expect(lines[0]).toBe("timestamp,message,filename,lineno,colno,source");
      expect(lines[1]).toContain("Uncaught");
      expect(lines[1]).toContain("app.js");
      expect(lines[1]).toContain("42");
    });
  });

  describe("filename generation", () => {
    it("derives a filename from the navigation URL hostname", () => {
      // The download function calls createElement("a") and sets a.download.
      // We capture the anchor element to verify the filename.
      const origCreate = document.createElement.bind(document);
      let capturedDownload = "";
      document.createElement = ((tag: string) => {
        const el = origCreate(tag);
        if (tag === "a") {
          (el as HTMLAnchorElement).click = vi.fn();
          Object.defineProperty(el, "download", {
            set: (v: string) => {
              capturedDownload = v;
            },
            get: () => capturedDownload,
          });
        }
        return el;
      }) as typeof document.createElement;

      exportDevToolsJSON(minimalState);
      expect(capturedDownload).toMatch(/^example\.com_/);
      expect(capturedDownload).toMatch(/\.json$/);
    });
  });
});
