// Network waterfall export — HAR 1.2 JSON and CSV.
//
// Pure functions (no DOM, no store) so they unit-test cleanly; the
// NetworkTab toolbar wires them to a real download.

import type { DevtoolsResource, DevtoolsNavigation } from "../../store/devtoolsStore";

interface HarTimings {
  blocked: number;
  dns: number;
  connect: number;
  send: number;
  wait: number;
  receive: number;
  ssl: number;
}

/** Build a HAR 1.2 log object from captured resources. */
export function buildHar(
  resources: DevtoolsResource[],
  navigation: DevtoolsNavigation | null,
): Record<string, unknown> {
  const entries = resources.map((r) => {
    const timings: HarTimings = {
      blocked: -1,
      dns: Math.max(0, r.dnsEnd - r.dnsStart),
      connect: Math.max(0, r.connectEnd - r.connectStart),
      send: 0,
      wait: Math.max(0, r.ttfb),
      receive: Math.max(0, r.responseEnd - r.responseStart),
      ssl: -1,
    };
    const entry = {
      pageref: "page_1",
      startedDateTime: new Date(r.startTime).toISOString(),
      time: r.duration,
      request: {
        method: "GET",
        url: r.name,
        httpVersion: r.nextHopProtocol || "unknown",
        headers: [],
        queryString: [],
        headersSize: -1,
        bodySize: 0,
      },
      response: {
        status: 200,
        statusText: "",
        httpVersion: r.nextHopProtocol || "unknown",
        headers: [],
        content: {
          size: r.decodedBodySize || 0,
          compression: Math.max(0, (r.encodedBodySize || 0) - (r.decodedBodySize || 0)),
          mimeType: "",
        },
        redirectURL: "",
        headersSize: -1,
        bodySize: r.transferSize || 0,
      },
      cache: {},
      timings,
      _resourceType: r.initiatorType,
      _transferSize: r.transferSize || 0,
      _encodedBodySize: r.encodedBodySize || 0,
    };
    return entry;
  });

  return {
    log: {
      version: "1.2",
      creator: { name: "Jambubrowser DevTools", version: "3.3.0" },
      pages: navigation
        ? [
            {
              startedDateTime: new Date(navigation.ttfb ? Date.now() - navigation.ttfb : Date.now()).toISOString(),
              id: "page_1",
              title: navigation.url,
              pageTimings: {
                onContentLoad: navigation.domContentLoaded || -1,
                onLoad: navigation.load || -1,
              },
            },
          ]
        : [],
      entries,
    },
  };
}

const CSV_HEADERS = [
  "url",
  "initiatorType",
  "startTimeMs",
  "durationMs",
  "ttfbMs",
  "dnsMs",
  "connectMs",
  "receiveMs",
  "transferSizeBytes",
  "encodedBodySizeBytes",
  "decodedBodySizeBytes",
  "protocol",
] as const;

function csvEscape(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

/** Build a CSV string from captured resources. */
export function buildCsv(resources: DevtoolsResource[]): string {
  const rows = resources.map((r) =>
    [
      r.name,
      r.initiatorType,
      String(Math.round(r.startTime)),
      String(Math.round(r.duration)),
      String(Math.round(r.ttfb || 0)),
      String(Math.round(Math.max(0, r.dnsEnd - r.dnsStart))),
      String(Math.round(Math.max(0, r.connectEnd - r.connectStart))),
      String(Math.round(Math.max(0, r.responseEnd - r.responseStart))),
      String(r.transferSize || 0),
      String(r.encodedBodySize || 0),
      String(r.decodedBodySize || 0),
      r.nextHopProtocol || "",
    ].map(csvEscape).join(","),
  );
  return [CSV_HEADERS.join(","), ...rows].join("\n");
}

/** Trigger a browser download of `content` as a file. */
export function downloadTextFile(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
