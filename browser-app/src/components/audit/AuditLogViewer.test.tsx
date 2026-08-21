import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock localFetch
const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
  createWebSocket: vi.fn(() => ({
    close: vi.fn(),
    onmessage: null,
  })),
}));

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
  LayoutGroup: ({ children }: any) => <>{children}</>,
}));

describe("AuditLogViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Return a fresh Response each call so body isn't consumed twice
    mockLocalFetch.mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            entries: [
              {
                id: "1",
                timestamp: Date.now() / 1000,
                category: "security",
                action: "login_attempt",
                details: { user: "admin" },
                hash: "abc123",
              },
            ],
            total: 1,
          }),
          { status: 200 }
        )
      )
    );
  });

  it("renders the Audit Log heading", async () => {
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    expect(screen.getByText("Audit Log")).toBeDefined();
  });

  it("displays audit entries after loading", async () => {
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    expect(await screen.findByText("login_attempt")).toBeDefined();
    expect(screen.getByText("security")).toBeDefined();
  });

  it("shows category filter dropdown", async () => {
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    expect(screen.getByText("All")).toBeDefined();
    expect(screen.getByText("Security")).toBeDefined();
    expect(screen.getByText("Privacy")).toBeDefined();
  });

  it("shows limit selector", async () => {
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    expect(screen.getByText("50 entries")).toBeDefined();
  });

  it("displays entry details as JSON", async () => {
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    expect(await screen.findByText(/user.*admin/)).toBeDefined();
  });

  it("shows hash for entries", async () => {
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    expect(await screen.findByText(/abc123/)).toBeDefined();
  });

  it("shows empty state when no entries", async () => {
    mockLocalFetch.mockResolvedValue(
      new Response(JSON.stringify({ entries: [] }), { status: 200 })
    );
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    expect(await screen.findByText("No audit entries.")).toBeDefined();
  });

  it("shows refresh button", async () => {
    const { AuditLogViewer } = await import("./AuditLogViewer");
    render(<AuditLogViewer />);
    // Refresh button is an icon button, find it by the RefreshCw icon
    const buttons = screen.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(0);
  });
});
