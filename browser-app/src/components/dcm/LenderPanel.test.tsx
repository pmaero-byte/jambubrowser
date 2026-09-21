import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
  engineOrigin: () => "http://test-engine",
}));

import {
  LenderPanel,
  lendUrl,
  normalizePeerId,
  rosterJobsTotal,
  rosterPaidTotal,
  rosterPeerState,
} from "./LenderPanel";

const NOW = 1_800_000_000_000;

const STATUS = { base_url: "http://127.0.0.1:3001", reachable: true };

const ROSTER = {
  success: true,
  count: 2,
  peers: [
    {
      peerId: "ios-safari-lender",
      did: "did:decentracode:ios-safari-lender",
      ua: "Mozilla/5.0 (iPhone)",
      jobsDone: 1,
      earnedDct: 0.010036864,
      lastSeen: NOW - 5_000,
    },
    {
      peerId: "old-laptop",
      did: "did:decentracode:old-laptop",
      jobsDone: 4,
      earnedDct: 0.0004,
      lastSeen: NOW - 5 * 60_000,
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function defaultFetch(path: string): Promise<Response> {
  if (path === "/dcm/status") return Promise.resolve(jsonResponse(STATUS));
  if (path === "/dcm/realtime/peers") return Promise.resolve(jsonResponse(ROSTER));
  if (path.startsWith("/dcm/earnings/")) {
    return Promise.resolve(jsonResponse({ did: "did:decentracode:jambu-lender", pendingDct: 3.5 }));
  }
  if (path.startsWith("/dcm/token/balance/")) {
    return Promise.resolve(jsonResponse({ did: "did:decentracode:jambu-lender", balance: 42.25 }));
  }
  return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
}

describe("lender panel helpers", () => {
  it("classifies lenders as live or stale from the node's heartbeat", () => {
    expect(rosterPeerState({ lastSeen: NOW - 1_000 }, NOW)).toBe("live");
    expect(rosterPeerState({ lastSeen: NOW - 5 * 60_000 }, NOW)).toBe("stale");
    // No lastSeen at all: the node only reports peers it holds, so say live
    // rather than inventing a stale reading.
    expect(rosterPeerState({ peerId: "x" }, NOW)).toBe("live");
  });

  it("sums what the node reports it paid, never a client-side estimate", () => {
    const total = rosterPaidTotal(ROSTER.peers);
    expect(total).toBeCloseTo(0.010436864, 12);
    expect(rosterJobsTotal(ROSTER.peers)).toBe(5);
    expect(rosterPaidTotal([])).toBe(0);
  });

  it("builds the lender page URL with a stable peer id and DID", () => {
    const url = lendUrl("http://127.0.0.1:3001", "jambu-lender", "did:decentracode:jambu-lender");
    expect(url).toBe(
      "http://127.0.0.1:3001/peer/peer.html?auto=1&peerId=jambu-lender&did=did%3Adecentracode%3Ajambu-lender",
    );
    expect(lendUrl("http://127.0.0.1:3001/", "a", "b")).toBe("http://127.0.0.1:3001/peer/peer.html?auto=1&peerId=a&did=b");
    expect(lendUrl("", "a", "b")).toBeNull();
  });

  it("sanitizes a peer id so a pasted value cannot break the URL", () => {
    expect(normalizePeerId("  my laptop  ")).toBe("my-laptop");
    expect(normalizePeerId("a/b?c=d")).toBe("a-b-c-d");
    expect(normalizePeerId("   ")).toBe("jambu-lender");
  });
});

describe("LenderPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockImplementation(defaultFetch);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the roster with each lender's verified earnings", async () => {
    render(<LenderPanel />);
    expect(await screen.findByText("ios-safari-lender")).toBeTruthy();
    expect(screen.getByText("old-laptop")).toBeTruthy();
    expect(screen.getByText("2 live of 2")).toBeTruthy();
    expect(screen.getByText(/0\.010036864 DCT/)).toBeTruthy();
  });

  it("reads the DID's two ledgers separately instead of one blended number", async () => {
    render(<LenderPanel />);
    await waitFor(() => expect(screen.getByTestId("lender-balance").textContent).toContain("42.250000000"));
    expect(screen.getByTestId("lender-pending").textContent).toContain("3.500000000");
  });

  it("offers the node's own peer page as the lending action", async () => {
    render(<LenderPanel />);
    const link = (await screen.findByTestId("lender-open-peer-page")) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toContain("/peer/peer.html?auto=1");
    expect(link.getAttribute("href")).toContain("did%3Adecentracode%3Ajambu-lender");
  });

  it("refetches the ledgers when the operator changes the DID", async () => {
    render(<LenderPanel />);
    await screen.findByText("ios-safari-lender");
    const didInput = screen.getByLabelText("DID");
    await userEvent.clear(didInput);
    await userEvent.type(didInput, "did:decentracode:other");
    await waitFor(() =>
      expect(mockLocalFetch.mock.calls.some(([p]) => String(p).includes("did%3Adecentracode%3Aother"))).toBe(true),
    );
  });

  it("says the mesh is unreachable instead of showing an empty roster as success", async () => {
    mockLocalFetch.mockImplementation((path: string) =>
      path === "/dcm/status"
        ? Promise.resolve(jsonResponse(STATUS))
        : Promise.resolve(jsonResponse({ detail: "DCM node unreachable at http://127.0.0.1:3001" }, 502)),
    );
    render(<LenderPanel />);
    expect(await screen.findByText("No mesh node reachable")).toBeTruthy();
    expect(screen.getByText(/DCM node unreachable/)).toBeTruthy();
  });

  it("does not quote a fiat value for DCT anywhere", async () => {
    const { container } = render(<LenderPanel />);
    await screen.findByText("ios-safari-lender");
    expect(container.textContent || "").not.toMatch(/\$\s*\d/);
    expect(container.textContent).toContain("not live yet");
  });
});
