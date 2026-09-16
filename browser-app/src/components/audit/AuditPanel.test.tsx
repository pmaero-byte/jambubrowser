import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock the transport layer: streaming isn't exercised here (no audit run),
// but history / report / share / export all go through localFetch.
const mockLocalFetch = vi.hoisted(() => vi.fn());
const mockLocalFetchStream = vi.hoisted(() => vi.fn());

vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
  localFetchStream: mockLocalFetchStream,
  engineOrigin: () => "http://localhost:8001",
}));

vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

const HISTORY = {
  audits: [
    {
      id: 1,
      url: "https://app.example.com",
      title: "Example App",
      mode: "quick",
      total_findings: 3,
      critical_count: 1,
      high_count: 1,
      medium_count: 1,
      low_count: 0,
      info_count: 0,
      created_at: 1787000000,
      share_token: null,
    },
  ],
  total: 1,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function defaultFetch(path: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method || "GET").toUpperCase();
  if (path.startsWith("/audit/history?") && method === "GET") {
    return Promise.resolve(jsonResponse(HISTORY));
  }
  if (path === "/audit/history/1/share" && method === "POST") {
    return Promise.resolve(jsonResponse({
      share_token: "tok123",
      share_url: "/audit/shared/tok123",
    }));
  }
  if (path === "/audit/report/1") {
    return Promise.resolve(new Response(
      "<!doctype html><html><body><h1>Jambubrowser Audit Report</h1></body></html>",
      { status: 200, headers: { "Content-Type": "text/html" } },
    ));
  }
  if (path.startsWith("/audit/export/")) {
    return Promise.resolve(new Response("{}", { status: 200 }));
  }
  return Promise.resolve(jsonResponse({}));
}

describe("AuditPanel — history / report / share / export", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockImplementation(defaultFetch);
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
  });

  it("loads and expands recent audits", async () => {
    const user = userEvent.setup();
    const { AuditPanel } = await import("./AuditPanel");
    render(<AuditPanel />);

    const header = await screen.findByText("Recent audits");
    await user.click(header);

    expect(await screen.findByText("https://app.example.com")).toBeDefined();
    expect(screen.getByText("3 findings")).toBeDefined();
    expect(screen.getByText("1 crit")).toBeDefined();
    expect(mockLocalFetch).toHaveBeenCalledWith("/audit/history?limit=10");
  });

  it("hides the history section when there are no audits", async () => {
    mockLocalFetch.mockImplementation((path: string) => {
      if (path.startsWith("/audit/history?")) {
        return Promise.resolve(jsonResponse({ audits: [], total: 0 }));
      }
      return Promise.resolve(jsonResponse({}));
    });
    const { AuditPanel } = await import("./AuditPanel");
    render(<AuditPanel />);

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith("/audit/history?limit=10");
    });
    expect(screen.queryByText("Recent audits")).toBeNull();
  });

  it("opens the HTML report modal for a history entry", async () => {
    const user = userEvent.setup();
    const { AuditPanel } = await import("./AuditPanel");
    render(<AuditPanel />);

    await user.click(await screen.findByText("Recent audits"));
    await user.click(await screen.findByLabelText("Report for audit 1"));

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith("/audit/report/1");
    });
    const iframe = await screen.findByTitle("Audit report");
    expect(iframe.getAttribute("srcdoc")).toContain("Jambubrowser Audit Report");
  });

  it("creates a share link and shows it", async () => {
    const user = userEvent.setup();
    const { AuditPanel } = await import("./AuditPanel");
    render(<AuditPanel />);

    await user.click(await screen.findByText("Recent audits"));
    await user.click(await screen.findByLabelText("Share audit 1"));

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith(
        "/audit/history/1/share",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(
      await screen.findByText("http://localhost:8001/audit/shared/tok123/report"),
    ).toBeDefined();
  });

  it("downloads an export for a history entry", async () => {
    const user = userEvent.setup();
    const { AuditPanel } = await import("./AuditPanel");
    render(<AuditPanel />);

    await user.click(await screen.findByText("Recent audits"));
    await user.click(await screen.findByLabelText("Download SARIF for audit 1"));

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith("/audit/export/sarif?audit_id=1");
    });
  });

  it("still renders the empty-state prompt before any audit", async () => {
    const { AuditPanel } = await import("./AuditPanel");
    render(<AuditPanel />);
    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith("/audit/history?limit=10");
    });
    expect(screen.getByText("Audit any webapp")).toBeDefined();
  });
});
