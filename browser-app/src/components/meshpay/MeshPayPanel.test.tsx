import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
  engineOrigin: () => "http://test-engine",
}));

vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

const AUDIT = {
  node_total_entries: 4,
  window: { requested: 200, returned: 4, truncated: false },
  verification: {
    valid: true,
    checked: 4,
    broken_at: null,
    broken_reason: null,
    head_hash: "deadbeef".repeat(8),
    window_truncated: false,
    kinds: { usage: 3, "inference-charge": 1 },
  },
  dcm_verification: { valid: true, entries: 4 },
  agreement: true,
  epochs: [
    {
      index: 0,
      from_index: 0,
      to_index: 3,
      receipts: 4,
      root: "cafe".repeat(16),
      providers: [{ nodeId: "peer-a", accruedDct: 0.05, receipts: 2 }],
    },
  ],
  payout: {
    epoch: { index: 0, receipts: 4, root: "cafe".repeat(16) },
    dct_usd_rate: 0.01,
    protocol_fee_pct: 0.15,
    rate_note: "DCT→USD rate and fee are MeshPay configuration, not an oracle.",
    providers: [
      {
        nodeId: "peer-a", receipts: 2, grossDct: 0.05,
        feeDct: 0.0075, netDct: 0.0425, usdc: 0.000425,
      },
    ],
    totals: { grossDct: 0.05, feeDct: 0.0075, netDct: 0.0425, usdc: 0.000425 },
  },
  config: {
    cluster: "mock",
    rpc_url: "",
    has_keypair: false,
    dct_usd_rate: 0.01,
    protocol_fee_pct: 0.15,
    epoch_size: 50,
    transport: "mock (no chain transactions)",
  },
};

const ANCHORS = {
  anchors: [
    {
      id: 1, epoch: 0, root: "cafe".repeat(16), receipts: 4, cluster: "mock",
      transport: "mock", signature: "mock:abcdef0123456789", memo: "meshpay:v1:0:4:cafe",
      created_at: 1700000000, explorer_url: "", status: "verified", matches: true,
      current_root: "cafe".repeat(16),
    },
  ],
  count: 1,
  verified: 1,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function defaultFetch(path: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method || "GET").toUpperCase();
  if (path.startsWith("/meshpay/audit")) return Promise.resolve(jsonResponse(AUDIT));
  if (path.startsWith("/meshpay/anchors") && method === "GET") {
    return Promise.resolve(jsonResponse(ANCHORS));
  }
  if (path === "/meshpay/anchor" && method === "POST") {
    return Promise.resolve(jsonResponse({
      id: 2, epoch: 0, root: "beef".repeat(16), receipts: 3, cluster: "mock",
      transport: "mock", signature: "mock:9999", memo: "meshpay:v1:0:3:beef",
      created_at: 1700000001, explorer_url: "",
    }));
  }
  return Promise.resolve(jsonResponse({ status: "ok" }));
}

describe("MeshPayPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockImplementation(defaultFetch);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the heading and rate disclosure", async () => {
    const { MeshPayPanel } = await import("./MeshPayPanel");
    render(<MeshPayPanel />);
    expect(screen.getByText("MeshPay")).toBeDefined();
    expect(await screen.findByText(/configured, not an oracle/)).toBeDefined();
    expect(screen.getByText(/mock \(no chain transactions\)/)).toBeDefined();
  });

  it("shows the independently verified chain and agreement", async () => {
    const { MeshPayPanel } = await import("./MeshPayPanel");
    render(<MeshPayPanel />);
    expect(await screen.findByText(/Receipt chain verified/)).toBeDefined();
    expect(screen.getByText(/independent replay, 4 receipts/)).toBeDefined();
    expect(screen.getByText(/usage×3/)).toBeDefined();
  });

  it("warns when the chain is broken", async () => {
    mockLocalFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path.startsWith("/meshpay/audit")) {
        return Promise.resolve(jsonResponse({
          ...AUDIT,
          verification: {
            ...AUDIT.verification, valid: false, broken_at: 2,
            broken_reason: "invoice hash mismatch: expected aa…, got bb…",
          },
          dcm_verification: { valid: true, entries: 4 },
          agreement: false,
        }));
      }
      return defaultFetch(path, init);
    });
    const { MeshPayPanel } = await import("./MeshPayPanel");
    render(<MeshPayPanel />);
    expect(await screen.findByText(/Receipt chain BROKEN/)).toBeDefined();
    expect(screen.getByText("— DISAGREEMENT")).toBeDefined();
    expect(screen.getByText(/Broken at #2/)).toBeDefined();
  });

  it("renders the payout plan with USDC amounts", async () => {
    const { MeshPayPanel } = await import("./MeshPayPanel");
    render(<MeshPayPanel />);
    expect(await screen.findByText(/Payout plan — latest epoch #0/)).toBeDefined();
    expect(screen.getByText("0.000425")).toBeDefined();
    expect(screen.getByText("peer-a")).toBeDefined();
  });

  it("anchors an epoch and shows the new record", async () => {
    const user = userEvent.setup();
    const { MeshPayPanel } = await import("./MeshPayPanel");
    render(<MeshPayPanel />);
    await screen.findByText(/Epochs/);

    await user.click(screen.getByRole("button", { name: /anchor/i }));

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith("/meshpay/anchor", expect.objectContaining({
        method: "POST",
      }));
    });
    expect(await screen.findByText(/Anchored roots \(2\)/)).toBeDefined();
  });

  it("surfaces audit errors", async () => {
    mockLocalFetch.mockImplementation((path: string) => {
      if (path.startsWith("/meshpay/audit")) {
        return Promise.resolve(jsonResponse(
          { detail: "DCM node unreachable — start it" }, 502,
        ));
      }
      return Promise.resolve(jsonResponse(ANCHORS));
    });
    const { MeshPayPanel } = await import("./MeshPayPanel");
    render(<MeshPayPanel />);
    expect(await screen.findByText(/DCM node unreachable/)).toBeDefined();
  });
});

describe("meshpay helpers", () => {
  it("shortHash truncates long hashes", async () => {
    const { shortHash } = await import("./MeshPayPanel");
    expect(shortHash(null)).toBe("—");
    expect(shortHash("abcd")).toBe("abcd");
    expect(shortHash("a".repeat(64), 4)).toBe("aaaa…aaaa");
  });

  it("anchorStatusColor maps verification statuses", async () => {
    const { anchorStatusColor } = await import("./MeshPayPanel");
    expect(anchorStatusColor("verified")).toContain("emerald");
    expect(anchorStatusColor("mismatch")).toContain("red");
    expect(anchorStatusColor(undefined)).toContain("muted");
  });
});
