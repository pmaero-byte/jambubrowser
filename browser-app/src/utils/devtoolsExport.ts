// DevTools data export utilities.
//
// Reads from `useDevtoolsStore` and produces downloadable files:
//   - JSON: full state dump (archival, comparison between audits)
//   - CSV:  network requests (open in Excel / Sheets / pandas)
//   - CSV:  console entries
//   - TXT:  console log stream (human-readable, tail-friendly)
//
// Usage in a component:
//   import { exportDevToolsJSON, exportNetworkCSV, exportConsoleCSV,
//            exportConsoleTXT } from "../../utils/devtoolsExport";
//   import { useDevtoolsStore } from "../../store/devtoolsStore";
//   const state = useDevtoolsStore.getState();
//   exportDevToolsJSON(state, "audit-2025-01-15");

import type { DevtoolsResource, DevtoolsConsoleEntry, DevtoolsErrorEntry } from "../store/devtoolsStore";
import { useDevtoolsStore } from "../store/devtoolsStore";

/**
 * Public type for the devtools state. Derived from the zustand store's
 * getState() return type so we don't need to export the internal
 * `DevtoolsState` interface from `devtoolsStore.ts`.
 */
export type DevtoolsState = ReturnType<typeof useDevtoolsStore.getState>;

const DEFAULT_DOMAIN = "jambubrowser-audit";

/** Slugify a URL for a default filename. */
function domainFromUrl(url: string | undefined | null): string {
  if (!url) return DEFAULT_DOMAIN;
  try {
    return new URL(url).hostname.replace(/^www\./, "").replace(/[^a-z0-9.-]/gi, "-");
  } catch {
    return DEFAULT_DOMAIN;
  }
}

/** ISO timestamp suitable for filenames (no colons). */
function timestampSlug(d = new Date()): string {
  return d.toISOString().replace(/[:.]/g, "-").replace(/Z$/, "");
}

/** Build a default filename like `myapp-com_2025-01-15T12-30-00.json`. */
function defaultFilename(state: DevtoolsState, ext: string): string {
  const domain = domainFromUrl(state.navigation?.url);
  return `${domain}_${timestampSlug()}.${ext}`;
}

/** Trigger a browser download of a Blob with the given filename. */
function downloadBlob(content: string, mime: string, filename: string): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Free the object URL on the next tick so the download has time to start.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ── JSON: full state dump ───────────────────────────────────────────────────

/** Export the entire devtools state as pretty-printed JSON. */
export function exportDevToolsJSON(state: DevtoolsState, filename?: string): void {
  const payload = {
    exportedAt: new Date().toISOString(),
    navigation: state.navigation,
    webVitals: {
      lcp: state.lcp,
      fcp: state.fcp,
      cls: state.cls,
    },
    perfSummary: state.perfSummary,
    resources: state.resources,
    consoleEntries: state.consoleEntries,
    errors: state.errors,
    longTasks: state.longTasks,
    counts: {
      resources: state.resources.length,
      consoleEntries: state.consoleEntries.length,
      errors: state.errors.length,
      longTasks: state.longTasks.length,
    },
  };
  downloadBlob(
    JSON.stringify(payload, null, 2),
    "application/json",
    filename ?? defaultFilename(state, "json"),
  );
}

// ── CSV helpers ─────────────────────────────────────────────────────────────

/** Quote a single CSV field per RFC 4180. */
function csvField(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = typeof value === "string" ? value : String(value);
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/** Join a row of values as a CSV line. */
function csvRow(values: unknown[]): string {
  return values.map(csvField).join(",");
}

/** Build a CSV string from a header + rows. */
function toCsv(headers: string[], rows: unknown[][]): string {
  const lines = [csvRow(headers), ...rows.map(csvRow)];
  return lines.join("\n") + "\n";
}

// ── CSV: network resources ──────────────────────────────────────────────────

const NETWORK_HEADERS = [
  "name",
  "initiatorType",
  "startTime",
  "duration",
  "dnsStart",
  "dnsEnd",
  "connectStart",
  "connectEnd",
  "ttfb",
  "responseStart",
  "responseEnd",
  "transferSize",
  "encodedBodySize",
  "decodedBodySize",
  "nextHopProtocol",
];

/** Export network resources as CSV (opens cleanly in Excel/Sheets/pandas). */
export function exportNetworkCSV(state: DevtoolsState, filename?: string): void {
  const rows: unknown[][] = state.resources.map((r: DevtoolsResource) => [
    r.name,
    r.initiatorType,
    r.startTime,
    r.duration,
    r.dnsStart,
    r.dnsEnd,
    r.connectStart,
    r.connectEnd,
    r.ttfb,
    r.responseStart,
    r.responseEnd,
    r.transferSize,
    r.encodedBodySize,
    r.decodedBodySize,
    r.nextHopProtocol,
  ]);
  downloadBlob(toCsv(NETWORK_HEADERS, rows), "text/csv", filename ?? defaultFilename(state, "network.csv"));
}

// ── CSV: console entries ────────────────────────────────────────────────────

const CONSOLE_HEADERS = ["timestamp", "level", "message"];

/** Export console entries as CSV. */
export function exportConsoleCSV(state: DevtoolsState, filename?: string): void {
  const rows: unknown[][] = state.consoleEntries.map((e: DevtoolsConsoleEntry) => [
    e.timestamp,
    e.level,
    e.message,
  ]);
  downloadBlob(
    toCsv(CONSOLE_HEADERS, rows),
    "text/csv",
    filename ?? defaultFilename(state, "console.csv"),
  );
}

// ── CSV: JS errors ──────────────────────────────────────────────────────────

const ERROR_HEADERS = ["timestamp", "message", "filename", "lineno", "colno", "source"];

/** Export captured JS errors as CSV. */
export function exportErrorsCSV(state: DevtoolsState, filename?: string): void {
  const rows: unknown[][] = state.errors.map((e: DevtoolsErrorEntry) => [
    e.timestamp ?? "",
    e.message,
    e.filename ?? "",
    e.lineno ?? "",
    e.colno ?? "",
    e.source ?? "",
  ]);
  downloadBlob(toCsv(ERROR_HEADERS, rows), "text/csv", filename ?? defaultFilename(state, "errors.csv"));
}

// ── TXT: human-readable console stream ──────────────────────────────────────

/** Format a console entry as a one-liner: `[2025-01-15T12:30:00.123Z] ERROR  msg`. */
function formatConsoleLine(entry: DevtoolsConsoleEntry): string {
  const ts = new Date(entry.timestamp).toISOString();
  const level = entry.level.toUpperCase().padEnd(5);
  return `[${ts}] ${level} ${entry.message}`;
}

/** Export console entries as a tail-friendly plain-text log file. */
export function exportConsoleTXT(state: DevtoolsState, filename?: string): void {
  const lines = state.consoleEntries.map(formatConsoleLine);
  downloadBlob(
    lines.join("\n") + "\n",
    "text/plain",
    filename ?? defaultFilename(state, "console.log"),
  );
}

// ── Convenience: bundle everything into a single JSON snapshot ─────────────

/**
 * Build a JSON snapshot string without downloading it.
 * Useful for tests, for piping into the audit history endpoint, or for
 * attaching to a support ticket.
 */
export function buildDevToolsSnapshot(state: DevtoolsState): string {
  return JSON.stringify(
    {
      exportedAt: new Date().toISOString(),
      navigation: state.navigation,
      webVitals: { lcp: state.lcp, fcp: state.fcp, cls: state.cls },
      perfSummary: state.perfSummary,
      resources: state.resources,
      consoleEntries: state.consoleEntries,
      errors: state.errors,
      longTasks: state.longTasks,
    },
    null,
    2,
  );
}
