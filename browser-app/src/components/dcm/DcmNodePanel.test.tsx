import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
  engineOrigin: () => "http://test-engine",
}));

const STATUS = {
  base_url: "http://127.0.0.1:3001",
  reachable: true,
  inference_status: {
    runtime: "candle-dense",
    ready: false,
    error: "Binary not found: /x/dcm-infer-candle",
    moe: { runtime: "python-moe", ready: true, available: true },
  },
  models: [
    { id: "qwen1.5-moe-a2.7b", name: "Qwen1.5-MoE", status: "ready", runtime: "python-moe" },
    { id: "glm-5.2", name: "GLM", status: "planned", runtime: "stub" },
  ],
  mesh_status: { nodeId: "node-abcdef123456", peers: [] },
};

const JOIN = {
  lanHosts: ["192.168.0.81"],
  ports: { peerPage: 3002, ws: 9501, http: 9502 },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function defaultFetch(path: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method || "GET").toUpperCase();
  if (path === "/dcm/status") return Promise.resolve(jsonResponse(STATUS));
  if (path === "/dcm/join-info") return Promise.resolve(jsonResponse(JOIN));
  if (path === "/dcm/infer" && method === "POST") {
    return Promise.resolve(jsonResponse({
      content: "Hello from the mesh", model: "qwen1.5-moe-a2.7b",
      usage: { completion_tokens: 4 },
    }));
  }
  return Promise.resolve(jsonResponse({ status: "ok" }));
}

describe("DcmNodePanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockImplementation(defaultFetch);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders node status with MoE readiness and model counts", async () => {
    const { DcmNodePanel } = await import("./DcmNodePanel");
    render(<DcmNodePanel />);
    expect(screen.getByText("DCM Node")).toBeDefined();
    expect(await screen.findByText(/python-moe ready/)).toBeDefined();
    expect(screen.getByText("1/2")).toBeDefined();
    expect(screen.getByText("node-abcdef12345")).toBeDefined();
  });

  it("shows the LAN join URL from join-info", async () => {
    const { DcmNodePanel } = await import("./DcmNodePanel");
    render(<DcmNodePanel />);
    expect(
      await screen.findByText("http://192.168.0.81:3002/node.html"),
    ).toBeDefined();
  });

  it("runs a prompt and shows the completion", async () => {
    const user = userEvent.setup();
    const { DcmNodePanel } = await import("./DcmNodePanel");
    render(<DcmNodePanel />);
    await screen.findByText(/python-moe ready/);

    await user.type(screen.getByPlaceholderText("Ask the mesh…"), "hi");
    await user.click(screen.getByRole("button", { name: /run/i }));

    expect(await screen.findByText("Hello from the mesh")).toBeDefined();
    expect(mockLocalFetch).toHaveBeenCalledWith("/dcm/infer", expect.objectContaining({
      method: "POST",
    }));
  });

  it("shows the engine's actionable error when inference fails", async () => {
    const user = userEvent.setup();
    mockLocalFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/dcm/infer") {
        return Promise.resolve(jsonResponse(
          { detail: "DCM 500: dcm-infer-candle ENOENT (build backend/p2pd binaries)" },
          502,
        ));
      }
      return defaultFetch(path, init);
    });
    const { DcmNodePanel } = await import("./DcmNodePanel");
    render(<DcmNodePanel />);
    await screen.findByText(/python-moe ready/);

    await user.type(screen.getByPlaceholderText("Ask the mesh…"), "hi");
    await user.click(screen.getByRole("button", { name: /run/i }));

    expect(await screen.findByText(/build backend\/p2pd binaries/)).toBeDefined();
  });

  it("tells the user how to start an unreachable node", async () => {
    mockLocalFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/dcm/status") {
        return Promise.resolve(jsonResponse({
          base_url: "http://127.0.0.1:3001", reachable: false,
        }));
      }
      return defaultFetch(path, init);
    });
    const { DcmNodePanel } = await import("./DcmNodePanel");
    render(<DcmNodePanel />);
    expect(await screen.findByText(/Node unreachable/)).toBeDefined();
    expect(screen.getByText(/npm start/)).toBeDefined();
  });
});

describe("dcm helpers", () => {
  it("dcmReadiness prefers the ready secondary runtime", async () => {
    const { dcmReadiness } = await import("./DcmNodePanel");
    const readiness = dcmReadiness(STATUS as any);
    expect(readiness.ready).toBe(true);
    expect(readiness.label).toBe("python-moe ready");
    expect(readiness.detail).toContain("dcm-infer-candle");
  });

  it("dcmReadiness reports not-ready with the cause", async () => {
    const { dcmReadiness } = await import("./DcmNodePanel");
    const readiness = dcmReadiness({
      inference_status: { runtime: "x", ready: false, error: "binary missing" },
    } as any);
    expect(readiness.ready).toBe(false);
    expect(readiness.label).toBe("not ready");
  });

  it("availableModels filters by available/status", async () => {
    const { availableModels } = await import("./DcmNodePanel");
    expect(availableModels(STATUS.models as any).map((m) => m.id)).toEqual([
      "qwen1.5-moe-a2.7b",
    ]);
    expect(availableModels(undefined)).toEqual([]);
  });

  it("peerCount handles list, map and number shapes", async () => {
    const { peerCount } = await import("./DcmNodePanel");
    expect(peerCount({ peers: [1, 2] })).toBe(2);
    expect(peerCount({ peers: { a: {}, b: {} } })).toBe(2);
    expect(peerCount({ peers: 3 })).toBe(3);
    expect(peerCount(undefined)).toBe(0);
  });
});
