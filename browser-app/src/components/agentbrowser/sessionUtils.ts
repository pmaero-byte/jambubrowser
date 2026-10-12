// Pure helpers for the Agent Browser pane.
//
// The pane itself is React; everything that can be reasoned about without a
// renderer lives here so it can be unit-tested — the same split the repo uses
// for findInPage.ts / takeover.ts. These shapes mirror the engine's Greenwich
// bridge: /ax-tree, /act-by-ref, /events and WS /stream.

export interface AxNode {
  /** Integer @ref the engine's act-by-ref expects. */
  ref: number;
  /** Resolved role: textbox, searchbox, button, link, combobox … */
  role: string;
  name: string;
  href: string;
  value: string;
  /** (disabled) / (focused) / [frame:n] markers, in tree order. */
  flags: string[];
  /** The original line, kept for tooltips. */
  raw: string;
}

const AX_LINE =
  /^\s*\[@(\d+):\s*([\w-]+)\s*"([^"]*)"((?:[^\]]|\](?!\s*$))*)\]\s*$/;

/**
 * Parse the engine's pruned AX tree into rows.
 *
 * Tree lines look like:
 *   [Root: document]
 *     [@2: textbox "Search manuscripts…" value="agathiyar" (focused)]
 *     [@4: link "Verse 0001 Commentary" href="/verse/1"]
 * Anything that does not match is skipped rather than guessed at — a row the
 * user can click must map to a ref the engine will accept.
 */
export function parseAxTree(tree: string): AxNode[] {
  const nodes: AxNode[] = [];
  for (const line of (tree || "").split("\n")) {
    const match = AX_LINE.exec(line);
    if (!match) continue;
    const [, refText, role, name, tail] = match;
    const href = /href="([^"]*)"/.exec(tail)?.[1] ?? "";
    const value = /value="([^"]*)"/.exec(tail)?.[1] ?? "";
    const flags: string[] = [];
    if (/\(disabled\)/.test(tail)) flags.push("disabled");
    if (/\(focused\)/.test(tail)) flags.push("focused");
    const frame = /\[frame:(\d+)\]/.exec(tail)?.[1];
    if (frame) flags.push(`frame:${frame}`);
    nodes.push({
      ref: Number(refText),
      role,
      name,
      href,
      value,
      flags,
      raw: line.trim(),
    });
  }
  return nodes;
}

/** Host of a URL, or "" when it has none (file:, about:). */
export function hostOfUrl(raw: string): string {
  try {
    return new URL(raw).hostname.toLowerCase();
  } catch {
    return "";
  }
}

/** Add a scheme so "example.com" works from the address bar. */
export function normalizeTargetUrl(raw: string): string {
  const trimmed = (raw || "").trim();
  if (!trimmed) return "about:blank";
  if (/^(https?:|file:|about:)/i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

/** Allowlist for a session opened on this URL (exact host only, fail closed). */
export function allowDomainsFor(raw: string): string[] {
  const host = hostOfUrl(raw);
  return host ? [host] : [];
}

export interface SessionEvent {
  kind: string;
  text: string;
  at: number;
}

/**
 * Human line for one payload from /browser/sessions/{id}/events.
 *
 * Accepts either the SSE envelope the engine sends
 * (``{event, session_id, data, timestamp}``) or a bare payload — callers have
 * handed over both, and reading the wrong level silently renders blank URLs.
 * Unknown events still surface (kind + compact JSON) so nothing is swallowed.
 */
export function formatSessionEvent(
  event: string,
  data: Record<string, unknown> | null | undefined,
  now: number = Date.now(),
): SessionEvent {
  const raw = (data || {}) as Record<string, unknown>;
  const inner = raw.data;
  const d = (inner && typeof inner === "object" ? inner : raw) as Record<string, unknown>;
  const kind = String(raw.event || event);
  const url = String(d.url || "");
  const title = String(d.title || "");
  let text: string;
  switch (kind) {
    case "ready":
      return { kind, text: "Telemetry connected", at: now };
    case "nav:start":
      text = `Navigating to ${url || "…"}`;
      break;
    case "nav:domcontentloaded":
      text = title ? `Loaded: ${title}` : `DOM ready: ${url}`;
      break;
    case "nav:networkidle":
      text = "Page settled (network idle)";
      break;
    case "audit:step":
      text = `${String(d.agent || "agent")}: ${String(d.finding || "")}` +
        (d.severity ? ` [${String(d.severity)}]` : "");
      break;
    case "error":
      text = `⚠ ${String(d.message || "unknown error")}`;
      break;
    default:
      text = `${kind}${Object.keys(d).length ? ` ${JSON.stringify(d).slice(0, 120)}` : ""}`;
  }
  return { kind, text, at: now };
}

/** data: URL for a frame payload ("" when there is nothing to show). */
export function frameToDataUrl(base64: string): string {
  return base64 ? `data:image/jpeg;base64,${base64}` : "";
}

export interface PagePoint {
  x: number;
  y: number;
}

/**
 * Map a click on the rendered <img> onto page coordinates.
 *
 * The screencast frame is the viewport at device scale, so the rendered size
 * and the page size differ; without this scaling a click near the right edge
 * of the pane lands somewhere else in the page. Coordinates are clamped to the
 * page box so a stray drag cannot produce an out-of-viewport gesture.
 */
export function pointerToPageCoords(
  event: { clientX: number; clientY: number },
  element: { getBoundingClientRect(): { left: number; top: number; width: number; height: number } },
  page: { width: number; height: number },
): PagePoint {
  const rect = element.getBoundingClientRect();
  if (!rect.width || !rect.height || !page.width || !page.height) {
    return { x: 0, y: 0 };
  }
  const x = ((event.clientX - rect.left) / rect.width) * page.width;
  const y = ((event.clientY - rect.top) / rect.height) * page.height;
  return {
    x: Math.max(0, Math.min(page.width, Math.round(x))),
    y: Math.max(0, Math.min(page.height, Math.round(y))),
  };
}
