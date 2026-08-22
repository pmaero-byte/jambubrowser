import { describe, it, expect, vi } from "vitest";
import { buildHar, buildCsv, downloadTextFile } from "./networkExport";
import type { DevtoolsResource, DevtoolsNavigation } from "../../store/devtoolsStore";

function makeResource(overrides: Partial<DevtoolsResource> = {}): DevtoolsResource {
  return {
    name: "https://example.com/app.js",
    initiatorType: "script",
    startTime: 100,
    duration: 250,
    dnsStart: 100,
    dnsEnd: 130,
    connectStart: 130,
    connectEnd: 180,
    ttfb: 60,
    responseStart: 240,
    responseEnd: 350,
    transferSize: 4096,
    encodedBodySize: 3800,
    decodedBodySize: 12000,
    nextHopProtocol: "h2",
    ...overrides,
  };
}

describe("networkExport", () => {
  it("builds a HAR 1.2 structure with creator and entries", () => {
    const har = buildHar([makeResource()], null) as any;
    expect(har.log.version).toBe("1.2");
    expect(har.log.creator.name).toContain("Jambubrowser");
    expect(har.log.entries).toHaveLength(1);
    expect(har.log.pages).toHaveLength(0);
  });

  it("maps timing fields into HAR timings", () => {
    const har = buildHar([makeResource()], null) as any;
    const entry = har.log.entries[0];
    expect(entry.timings.dns).toBe(30);
    expect(entry.timings.connect).toBe(50);
    expect(entry.timings.wait).toBe(60);
    expect(entry.request.url).toBe("https://example.com/app.js");
    expect(entry._resourceType).toBe("script");
  });

  it("includes page info when navigation is present", () => {
    const nav: DevtoolsNavigation = {
      url: "https://example.com/",
      domContentLoaded: 500,
      load: 900,
      domInteractive: 400,
      ttfb: 80,
      dnsTime: 10,
      tcpTime: 30,
      transferSize: 50000,
      decodedBodySize: 200000,
      type: "navigate",
    };
    const har = buildHar([makeResource()], nav) as any;
    expect(har.log.pages).toHaveLength(1);
    expect(har.log.pages[0].title).toBe("https://example.com/");
    expect(har.log.pages[0].pageTimings.onLoad).toBe(900);
  });

  it("builds CSV with headers and one row per resource", () => {
    const csv = buildCsv([makeResource(), makeResource({ name: "https://cdn.test/x.css", initiatorType: "css" })]);
    const lines = csv.split("\n");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toContain("url,initiatorType");
    expect(lines[1]).toContain("https://example.com/app.js");
    expect(lines[2]).toContain("x.css");
  });

  it("escapes commas and quotes in CSV fields", () => {
    const csv = buildCsv([makeResource({ name: 'https://test.com/a?b="x,y"' })]);
    expect(csv.split("\n")[1]).toContain('"https://test.com/a?b=""x,y"""');
  });

  it("downloadTextFile triggers an anchor click with object URL", () => {
    const click = vi.fn();
    const revoke = vi.fn();
    const createObjURL = vi.fn(() => "blob:fake");
    (URL as any).createObjectURL = createObjURL;
    (URL as any).revokeObjectURL = revoke;

    const anchor = {
      href: "",
      download: "",
      click,
      remove: vi.fn(),
    };
    const appendChild = vi.fn();
    (document.body as any).appendChild = appendChild;
    (document as any).createElement = vi.fn(() => anchor);

    downloadTextFile("hello", "out.json", "application/json");

    expect(createObjURL).toHaveBeenCalled();
    expect(anchor.download).toBe("out.json");
    expect(click).toHaveBeenCalled();
    expect(revoke).toHaveBeenCalledWith("blob:fake");
  });
});
