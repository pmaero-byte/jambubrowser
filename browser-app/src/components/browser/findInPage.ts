// ── Find-in-page core ────────────────────────────────────────────
// `findInPageCore` / `clearFindCore` run in TWO places:
//   1. In vitest, directly against jsdom (see findInPage.test.ts).
//   2. Inside the live headless-Chrome page — ChromiumPane injects them
//      via Function.prototype.toString() through the existing
//      `browser_evaluate` CDP command (Runtime.evaluate).
// Because of (2) both functions must be fully self-contained: no imports,
// no module-scope runtime references, only DOM APIs the page provides.

export type FindDirection = "init" | "next" | "prev";

export interface FindResult {
  total: number;
  /** 1-based index of the active match; 0 when there are no matches. */
  active: number;
}

/**
 * Scan the page for `query`, highlight every match via the CSS Highlights
 * API (falling back to the native text selection for the active match),
 * and move the active match according to `direction`.
 *
 * State (ranges + active index) persists on `window.__jambuFindState`
 * between evaluations, so repeated calls with the same query cycle
 * through the matches instead of re-scanning.
 */
export function findInPageCore(query: string, direction: FindDirection): FindResult {
  const STATE_KEY = "__jambuFindState";
  const HL_ALL = "jambu-find-all";
  const HL_CURRENT = "jambu-find-current";
  interface FindState { query: string; ranges: Range[]; active: number }
  const w = window as unknown as Record<string, FindState | undefined>;

  const clearHighlights = () => {
    try {
      const h = (CSS as unknown as { highlights?: Map<string, unknown> }).highlights;
      if (h) { h.delete(HL_ALL); h.delete(HL_CURRENT); }
    } catch { /* CSS Highlights API unavailable */ }
  };

  if (!query || !document.body) {
    clearHighlights();
    w[STATE_KEY] = undefined;
    return { total: 0, active: 0 };
  }

  let state = w[STATE_KEY];
  if (!state || state.query.toLowerCase() !== query.toLowerCase()) {
    // Fresh scan: TreeWalker over text nodes, skipping non-rendered
    // elements. Matching is case-insensitive via toLowerCase(); note the
    // offset math assumes lowercasing preserves length (false for a few
    // unicode chars like 'İ' — an acceptable edge case for a find bar).
    const ranges: Range[] = [];
    const needle = query.toLowerCase();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const parent = (node as Text).parentElement;
      const tag = parent ? parent.tagName : "";
      if (tag !== "SCRIPT" && tag !== "STYLE" && tag !== "NOSCRIPT") {
        const hay = (node.nodeValue || "").toLowerCase();
        let idx = hay.indexOf(needle);
        while (idx !== -1) {
          const r = document.createRange();
          r.setStart(node, idx);
          r.setEnd(node, idx + needle.length);
          ranges.push(r);
          idx = hay.indexOf(needle, idx + needle.length);
        }
      }
      node = walker.nextNode();
    }
    state = { query, ranges, active: 0 };
    w[STATE_KEY] = state;
  } else if (direction !== "init" && state.ranges.length > 0) {
    const delta = direction === "prev" ? -1 : 1;
    state.active = (state.active + delta + state.ranges.length) % state.ranges.length;
  }

  const total = state.ranges.length;
  if (total > 0) {
    // Highlight colors, injected once.
    if (!document.getElementById("jambu-find-style")) {
      const style = document.createElement("style");
      style.id = "jambu-find-style";
      style.textContent =
        `::highlight(${HL_ALL}) { background-color: rgba(250, 204, 21, 0.35); color: inherit; }` +
        `::highlight(${HL_CURRENT}) { background-color: rgba(249, 115, 22, 0.6); color: inherit; }`;
      (document.head || document.documentElement).appendChild(style);
    }
    clearHighlights();
    try {
      const h = (CSS as unknown as { highlights?: Map<string, unknown> }).highlights;
      const Hl = (window as unknown as { Highlight?: new (...r: Range[]) => unknown }).Highlight;
      if (h && Hl) {
        h.set(HL_ALL, new Hl(...state.ranges));
        h.set(HL_CURRENT, new Hl(state.ranges[state.active]));
      }
    } catch { /* older Chromium without CSS Highlights — selection below still marks the hit */ }
    // Select the active match natively (works even without CSS Highlights)
    // and scroll it into view so the next screenshot shows it.
    try {
      const active = state.ranges[state.active];
      const sel = window.getSelection();
      if (sel) { sel.removeAllRanges(); sel.addRange(active); }
      const el = active.startContainer.parentElement;
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "center", inline: "nearest" });
    } catch { /* selection/scroll unsupported — highlights still applied */ }
  } else {
    clearHighlights();
  }
  return { total, active: total === 0 ? 0 : state.active + 1 };
}

/** Remove all find highlights and drop the persisted find state. */
export function clearFindCore(): void {
  const w = window as unknown as Record<string, unknown>;
  w.__jambuFindState = undefined;
  try {
    const h = (CSS as unknown as { highlights?: Map<string, unknown> }).highlights;
    if (h) { h.delete("jambu-find-all"); h.delete("jambu-find-current"); }
  } catch { /* CSS Highlights API unavailable */ }
  const sel = window.getSelection();
  if (sel) sel.removeAllRanges();
  const style = document.getElementById("jambu-find-style");
  if (style) style.remove();
}

// ── Expression builders (run in the app, not the page) ───────────
// Serialize the core functions into self-invoking expressions for the
// `browser_evaluate` CDP command. JSON.stringify safely escapes the args.

export function buildFindExpression(query: string, direction: FindDirection): string {
  return `(${findInPageCore.toString()})(${JSON.stringify(query)}, ${JSON.stringify(direction)})`;
}

export function buildClearFindExpression(): string {
  return `(${clearFindCore.toString()})()`;
}
