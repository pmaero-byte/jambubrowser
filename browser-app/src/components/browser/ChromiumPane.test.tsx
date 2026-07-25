import { describe, it, expect } from "vitest";
import { normalizeUrl, clientToPageCoords, isPdfUrl } from "./ChromiumPane";

describe("normalizeUrl", () => {
  it("passes through full URLs unchanged", () => {
    expect(normalizeUrl("https://example.com/path?q=1")).toBe("https://example.com/path?q=1");
    expect(normalizeUrl("http://localhost:8888/search")).toBe("http://localhost:8888/search");
    expect(normalizeUrl("about:blank")).toBe("about:blank");
  });

  it("prepends https:// to bare domains", () => {
    expect(normalizeUrl("example.com")).toBe("https://example.com");
    expect(normalizeUrl("docs.rs/serde")).toBe("https://docs.rs/serde");
  });

  it("falls back to the local SearXNG instance for search queries", () => {
    expect(normalizeUrl("hello world")).toBe(
      "http://localhost:8888/search?q=hello%20world"
    );
    expect(normalizeUrl("carnatic music?")).toBe(
      "http://localhost:8888/search?q=carnatic%20music%3F"
    );
  });

  it("returns about:blank for empty input", () => {
    expect(normalizeUrl("")).toBe("about:blank");
    expect(normalizeUrl("   ")).toBe("about:blank");
  });
});

describe("clientToPageCoords", () => {
  // 1280x800 capture rendered into a 640x400 container: exact fit, no letterbox.
  const exactFit = { left: 0, top: 0, width: 640, height: 400 };

  it("scales coordinates from rendered size to capture size", () => {
    expect(clientToPageCoords(320, 200, exactFit, 1280, 800)).toEqual({ x: 640, y: 400 });
    expect(clientToPageCoords(0, 0, exactFit, 1280, 800)).toEqual({ x: 0, y: 0 });
  });

  it("accounts for the container's position on screen", () => {
    const rect = { left: 100, top: 50, width: 640, height: 400 };
    expect(clientToPageCoords(100 + 320, 50 + 200, rect, 1280, 800)).toEqual({ x: 640, y: 400 });
  });

  it("accounts for object-contain letterboxing (pillarbox)", () => {
    // 1280x800 capture in a 740x400 container: scale = 0.5 (height-bound),
    // content is 640 wide, centered with 50px bars on each side.
    const rect = { left: 0, top: 0, width: 740, height: 400 };
    expect(clientToPageCoords(50 + 320, 200, rect, 1280, 800)).toEqual({ x: 640, y: 400 });
  });

  it("accounts for object-contain letterboxing (letterbox)", () => {
    // 1280x800 capture in a 640x500 container: scale = 0.5 (width-bound),
    // content is 400 tall, centered with 50px bars top and bottom.
    const rect = { left: 0, top: 0, width: 640, height: 500 };
    expect(clientToPageCoords(320, 50 + 200, rect, 1280, 800)).toEqual({ x: 640, y: 400 });
  });

  it("returns null for points in the letterbox margin", () => {
    const rect = { left: 0, top: 0, width: 740, height: 400 };
    expect(clientToPageCoords(10, 200, rect, 1280, 800)).toBeNull(); // left bar
    expect(clientToPageCoords(730, 200, rect, 1280, 800)).toBeNull(); // right bar
  });

  it("returns null for degenerate sizes", () => {
    expect(clientToPageCoords(10, 10, exactFit, 0, 800)).toBeNull();
    expect(clientToPageCoords(10, 10, { left: 0, top: 0, width: 0, height: 400 }, 1280, 800)).toBeNull();
  });
});

describe("isPdfUrl", () => {
  it("detects PDFs while ignoring query strings", () => {
    expect(isPdfUrl("https://example.com/paper.pdf")).toBe(true);
    expect(isPdfUrl("https://example.com/view?file=paper.pdf")).toBe(false);
  });
});
