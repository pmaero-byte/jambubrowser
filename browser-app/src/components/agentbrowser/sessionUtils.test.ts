import { describe, it, expect } from "vitest";
import {
  parseAxTree,
  hostOfUrl,
  normalizeTargetUrl,
  allowDomainsFor,
  formatSessionEvent,
  frameToDataUrl,
  pointerToPageCoords,
} from "./sessionUtils";

// The tree below is a verbatim sample from a live run against the engine
// (fixture page + a real Yaadhum asset), so the parser is pinned to output the
// engine actually produces rather than to a hand-written ideal.
const TREE = [
  "[Root: document]",
  '  [@1: textbox "Search manuscripts..." value="அகத்தியர்"]',
  '  [@2: button "Submit Search"]',
  '  [@3: link "Verse 0001 Commentary" href="file:///verse/1"]',
  '  [@4: button "Probe"]',
  '  [@5: textbox "Filter" (focused)]',
  '  [@6: button "Delete account" (disabled)]',
  '  [@7: button "In frame" [frame:2]]',
].join("\n");

describe("parseAxTree", () => {
  it("parses every element row with its ref and role", () => {
    const nodes = parseAxTree(TREE);
    expect(nodes.map((n) => [n.ref, n.role])).toEqual([
      [1, "textbox"],
      [2, "button"],
      [3, "link"],
      [4, "button"],
      [5, "textbox"],
      [6, "button"],
      [7, "button"],
    ]);
  });

  it("keeps names, hrefs and field values", () => {
    const [search, submit, link] = parseAxTree(TREE);
    expect(search.name).toBe("Search manuscripts...");
    expect(search.value).toBe("அகத்தியர்"); // unicode survives intact
    expect(submit.name).toBe("Submit Search");
    expect(link.href).toBe("file:///verse/1");
  });

  it("records disabled, focused and frame markers as flags", () => {
    const byRef = new Map(parseAxTree(TREE).map((n) => [n.ref, n]));
    expect(byRef.get(1)!.flags).toEqual([]);
    expect(byRef.get(5)!.flags).toEqual(["focused"]);
    expect(byRef.get(6)!.flags).toEqual(["disabled"]);
    expect(byRef.get(7)!.flags).toEqual(["frame:2"]);
  });

  it("skips the root line and anything unparseable", () => {
    const messy = "[Root: document]\n  not a tree line\n  [@9: button \"Ok\"]\n";
    const nodes = parseAxTree(messy);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].ref).toBe(9);
  });

  it("returns nothing for an empty tree", () => {
    expect(parseAxTree("")).toEqual([]);
  });
});

describe("url helpers", () => {
  it("normalises bare hosts to https", () => {
    expect(normalizeTargetUrl("example.com")).toBe("https://example.com");
    expect(normalizeTargetUrl(" http://a.test/x ")).toBe("http://a.test/x");
  });

  it("leaves non-http schemes alone and defaults to about:blank", () => {
    expect(normalizeTargetUrl("file:///tmp/x.html")).toBe("file:///tmp/x.html");
    expect(normalizeTargetUrl("")).toBe("about:blank");
  });

  it("reads the host and refuses to invent one for file URLs", () => {
    expect(hostOfUrl("https://Docs.Example.COM/a")).toBe("docs.example.com");
    expect(hostOfUrl("file:///tmp/x.html")).toBe("");
    expect(allowDomainsFor("https://example.com")).toEqual(["example.com"]);
    expect(allowDomainsFor("file:///tmp/x.html")).toEqual([]);
  });
});

describe("formatSessionEvent", () => {
  it("renders each navigation stage in words", () => {
    expect(formatSessionEvent("nav:start", { url: "https://a.test" }, 1).text)
      .toBe("Navigating to https://a.test");
    expect(formatSessionEvent("nav:domcontentloaded", { title: "Docs" }, 1).text)
      .toBe("Loaded: Docs");
    expect(formatSessionEvent("nav:networkidle", {}, 1).text)
      .toBe("Page settled (network idle)");
  });

  it("renders audit steps with agent and severity", () => {
    const evt = formatSessionEvent(
      "audit:step",
      { agent: "security", finding: "3 findings", severity: "medium" },
      7,
    );
    expect(evt.text).toBe("security: 3 findings [medium]");
    expect(evt.at).toBe(7);
  });

  it("marks errors and keeps unknown events visible", () => {
    expect(formatSessionEvent("error", { message: "boom" }, 1).text).toBe("⚠ boom");
    const other = formatSessionEvent("custom:thing", { a: 1 }, 1);
    expect(other.kind).toBe("custom:thing");
    expect(other.text).toContain("custom:thing");
  });

  it("unwraps the SSE envelope the engine actually sends", () => {
    // The frame is {event, session_id, data, timestamp}; reading fields at the
    // envelope level renders blank URLs (found by an e2e run).
    const envelope = {
      event: "nav:domcontentloaded",
      session_id: "bs-1",
      data: { url: "file:///tmp/x.html", title: "Agathiyar Archive" },
      timestamp: 1,
    };
    const evt = formatSessionEvent("nav:domcontentloaded", envelope, 5);
    expect(evt.kind).toBe("nav:domcontentloaded");
    expect(evt.text).toBe("Loaded: Agathiyar Archive");
    expect(evt.at).toBe(5);
  });
});

describe("frames", () => {
  it("builds a data URL only when there is a frame", () => {
    expect(frameToDataUrl("abc")).toBe("data:image/jpeg;base64,abc");
    expect(frameToDataUrl("")).toBe("");
  });
});

describe("pointerToPageCoords", () => {
  const el = (rect: Partial<DOMRect>) => ({
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 720, height: 450, ...rect }) as DOMRect,
  });

  it("scales a click from rendered size onto page size", () => {
    // 720x450 rendered, 1440x900 page (device scale 2): the centre stays the
    // centre and the top-right corner maps to the page's corner.
    expect(pointerToPageCoords({ clientX: 360, clientY: 225 }, el({}), { width: 1440, height: 900 }))
      .toEqual({ x: 720, y: 450 });
    expect(pointerToPageCoords({ clientX: 720, clientY: 0 }, el({}), { width: 1440, height: 900 }))
      .toEqual({ x: 1440, y: 0 });
  });

  it("accounts for an offset pane", () => {
    const offset = el({ left: 100, top: 50, width: 200, height: 100 });
    expect(pointerToPageCoords({ clientX: 100, clientY: 50 }, offset, { width: 400, height: 200 }))
      .toEqual({ x: 0, y: 0 });
    expect(pointerToPageCoords({ clientX: 200, clientY: 100 }, offset, { width: 400, height: 200 }))
      .toEqual({ x: 200, y: 100 });
  });

  it("clamps to the page box instead of emitting off-page gestures", () => {
    expect(pointerToPageCoords({ clientX: 900, clientY: 600 }, el({}), { width: 1440, height: 900 }))
      .toEqual({ x: 1440, y: 900 });
  });

  it("returns the origin when the pane or page has no size", () => {
    const collapsed = el({ width: 0, height: 0 });
    expect(pointerToPageCoords({ clientX: 5, clientY: 5 }, collapsed, { width: 100, height: 100 }))
      .toEqual({ x: 0, y: 0 });
    expect(pointerToPageCoords({ clientX: 5, clientY: 5 }, el({}), { width: 0, height: 0 }))
      .toEqual({ x: 0, y: 0 });
  });
});
