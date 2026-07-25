import { describe, it, expect, beforeEach } from "vitest";
import {
  findInPageCore, clearFindCore, buildFindExpression, buildClearFindExpression,
} from "./findInPage";

// findInPageCore runs inside the live page in production; here it runs
// against jsdom, which is exactly why the core is written as a portable
// self-contained function.

describe("findInPageCore", () => {
  beforeEach(() => {
    clearFindCore();
    document.body.innerHTML = "";
  });

  it("counts case-insensitive matches across text nodes", () => {
    document.body.innerHTML = "<p>Raga rock and RAGA roll</p><span>another raga</span>";
    const result = findInPageCore("raga", "init");
    expect(result).toEqual({ total: 3, active: 1 });
  });

  it("counts multiple matches within a single text node", () => {
    document.body.innerHTML = "<p>aaa aaa aaa</p>";
    expect(findInPageCore("aaa", "init").total).toBe(3);
  });

  it("skips script and style contents", () => {
    document.body.innerHTML = "<script>var needle = 1;</script><style>.needle{}</style><p>needle</p>";
    expect(findInPageCore("needle", "init").total).toBe(1);
  });

  it("cycles forward and backward with next/prev, wrapping around", () => {
    document.body.innerHTML = "<p>x one</p><p>x two</p><p>x three</p>";
    expect(findInPageCore("x", "init")).toEqual({ total: 3, active: 1 });
    expect(findInPageCore("x", "next")).toEqual({ total: 3, active: 2 });
    expect(findInPageCore("x", "next")).toEqual({ total: 3, active: 3 });
    expect(findInPageCore("x", "next")).toEqual({ total: 3, active: 1 }); // wraps
    expect(findInPageCore("x", "prev")).toEqual({ total: 3, active: 3 }); // wraps back
  });

  it("re-scans and resets the active match when the query changes", () => {
    document.body.innerHTML = "<p>alpha beta beta</p>";
    findInPageCore("beta", "next");
    const result = findInPageCore("alpha", "init");
    expect(result).toEqual({ total: 1, active: 1 });
  });

  it("returns 0/0 for an empty query and clears state", () => {
    document.body.innerHTML = "<p>something</p>";
    findInPageCore("something", "init");
    expect(findInPageCore("", "init")).toEqual({ total: 0, active: 0 });
  });

  it("returns 0/0 when nothing matches", () => {
    document.body.innerHTML = "<p>hello world</p>";
    expect(findInPageCore("zzz", "init")).toEqual({ total: 0, active: 0 });
  });

  it("clearFindCore drops persisted state so the next scan starts fresh", () => {
    document.body.innerHTML = "<p>loop loop</p>";
    findInPageCore("loop", "next");
    clearFindCore();
    expect(findInPageCore("loop", "init")).toEqual({ total: 2, active: 1 });
  });
});

describe("find expression builders", () => {
  it("embeds the core function and JSON-escaped arguments", () => {
    const expr = buildFindExpression('he said "hi"', "next");
    expect(expr).toContain('he said \\"hi\\"'); // JSON-escaped quotes
    expect(expr).toContain('"next"');
    expect(expr.startsWith("(")).toBe(true);
  });

  it("produces an evaluable expression (round-trip through eval in jsdom)", () => {
    document.body.innerHTML = "<p>round trip round</p>";
    clearFindCore();
    // eslint-disable-next-line no-eval
    const result = eval(buildFindExpression("round", "init")) as { total: number; active: number };
    expect(result).toEqual({ total: 2, active: 1 });
    clearFindCore();
  });

  it("builds a clear expression that runs without error", () => {
    // eslint-disable-next-line no-eval
    expect(() => eval(buildClearFindExpression())).not.toThrow();
  });
});
