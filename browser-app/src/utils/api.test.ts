import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  isTauri,
  fetchKnowledgeEntity,
  fetchKnowledgeGraph,
  searchKnowledge,
  fetchKnowledgeStats,
  fetchMissionResults,
} from "./api";

describe("api", () => {
  it("isTauri returns false outside Tauri", () => {
    expect(isTauri()).toBe(false);
  });
});

// ── Knowledge graph API client ──────────────────────────────────────────────

const originalFetch = globalThis.fetch;

function mockJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("fetchKnowledgeEntity", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("calls /knowledge/entity/{id} with the URL-encoded id", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ entity: {}, connections: [], connection_count: 0 }));
    await fetchKnowledgeEntity("foo bar/baz");
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/knowledge/entity/foo%20bar%2Fbaz");
  });

  it("returns the parsed JSON body on success", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      mockJsonResponse({
        entity: { id: "mlx", name: "MLX", type: "technology", occurrences: 3 },
        connections: [{ direction: "incoming", entity: "X", entity_type: "product", relation: "uses", evidence: "" }],
        connection_count: 1,
      }),
    );
    const result = await fetchKnowledgeEntity("mlx");
    expect(result.entity.name).toBe("MLX");
    expect(result.connections[0].direction).toBe("incoming");
  });

  it("throws a friendly error on 404", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({}, 404));
    await expect(fetchKnowledgeEntity("missing")).rejects.toThrow(/Entity not found: missing/);
  });

  it("throws a generic error on 500", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({}, 500));
    await expect(fetchKnowledgeEntity("x")).rejects.toThrow(/Failed to fetch entity \(500\)/);
  });
});

describe("fetchKnowledgeGraph", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("uses the default max_nodes of 100", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ nodes: [], links: [] }));
    await fetchKnowledgeGraph();
    expect(mockFetch.mock.calls[0][0]).toContain("max_nodes=100");
  });

  it("honors a custom max_nodes value", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ nodes: [], links: [] }));
    await fetchKnowledgeGraph(25);
    expect(mockFetch.mock.calls[0][0]).toContain("max_nodes=25");
  });

  it("returns nodes + links arrays", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      mockJsonResponse({
        nodes: [{ id: "a", label: "A", group: "entity" }],
        links: [{ source: "a", target: "b" }],
      }),
    );
    const result = await fetchKnowledgeGraph();
    expect(result.nodes).toHaveLength(1);
    expect(result.links).toHaveLength(1);
  });

  it("throws on non-OK response", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({}, 503));
    await expect(fetchKnowledgeGraph()).rejects.toThrow(/Failed to fetch graph \(503\)/);
  });
});

describe("searchKnowledge", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("URL-encodes the query and applies the default limit", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ results: [] }));
    await searchKnowledge("foo bar & baz");
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("query=foo%20bar%20%26%20baz");
    expect(url).toContain("limit=20");
  });

  it("honors a custom limit", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ results: [] }));
    await searchKnowledge("x", 5);
    expect(mockFetch.mock.calls[0][0]).toContain("limit=5");
  });

  it("returns the results array", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({ results: [{ id: "a" }] }));
    const result = await searchKnowledge("a");
    expect(result.results).toHaveLength(1);
  });

  it("throws on non-OK response", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({}, 400));
    await expect(searchKnowledge("x")).rejects.toThrow(/Search failed \(400\)/);
  });
});

describe("fetchKnowledgeStats", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("calls /knowledge/stats", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ entity_count: 10, relation_count: 20 }));
    await fetchKnowledgeStats();
    expect(mockFetch.mock.calls[0][0]).toContain("/knowledge/stats");
  });

  it("returns the parsed stats body", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      mockJsonResponse({ entity_count: 42, relation_count: 7, extra: "ignored" }),
    );
    const result = await fetchKnowledgeStats();
    expect(result.entity_count).toBe(42);
    expect(result.relation_count).toBe(7);
  });

  it("throws on non-OK response", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({}, 500));
    await expect(fetchKnowledgeStats()).rejects.toThrow(/Stats failed \(500\)/);
  });
});

// ── Mission results API client ──────────────────────────────────────────────

describe("fetchMissionResults", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("calls /mission/{id}/results with URL-encoded id and default limit", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ mission_id: "abc", results: [], count: 0 }));
    await fetchMissionResults("abc 123");
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("/mission/abc%20123/results");
    expect(url).toContain("limit=50");
  });

  it("honors a custom limit", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(mockJsonResponse({ mission_id: "x", results: [], count: 0 }));
    await fetchMissionResults("x", 10);
    expect(mockFetch.mock.calls[0][0]).toContain("limit=10");
  });

  it("returns the parsed response on success", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      mockJsonResponse({
        mission_id: "m1",
        results: [{ id: 1, run_at: 1700000000, result_text: "hello", success: true }],
        count: 1,
      }),
    );
    const result = await fetchMissionResults("m1");
    expect(result.mission_id).toBe("m1");
    expect(result.results).toHaveLength(1);
    expect(result.results[0].success).toBe(true);
    expect(result.count).toBe(1);
  });

  it("throws a friendly error on 404", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({}, 404));
    await expect(fetchMissionResults("missing")).rejects.toThrow(/Mission not found: missing/);
  });

  it("throws a generic error on 500", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJsonResponse({}, 500));
    await expect(fetchMissionResults("x")).rejects.toThrow(/Failed to fetch mission results \(500\)/);
  });
});
