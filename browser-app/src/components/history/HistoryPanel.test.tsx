import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock localFetch
const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
}));

const defaultHistoryStore = {
  entries: [
    { url: "https://example.com", title: "Example", visitedAt: Date.now() - 60000 },
    { url: "https://test.com", title: "Test Site", visitedAt: Date.now() - 3600000 },
  ],
  removeEntry: vi.fn(),
  clearAll: vi.fn(),
};

// Selector-aware zustand mock: components call useXStore((s) => s.field),
// so the mock must invoke the selector against the store snapshot.
const mockUseBrowsingHistoryStore = vi.hoisted(() =>
  vi.fn((selector?: (s: any) => any) =>
    selector ? selector(defaultHistoryStore) : defaultHistoryStore
  )
);
vi.mock("../../store/browsingHistoryStore", () => ({
  useBrowsingHistoryStore: mockUseBrowsingHistoryStore,
}));

const defaultAppStore = {
  setActiveTab: vi.fn(),
  addBrowserTab: vi.fn(),
};

const mockUseAppStore = vi.hoisted(() =>
  vi.fn((selector?: (s: any) => any) =>
    selector ? selector(defaultAppStore) : defaultAppStore
  )
);
vi.mock("../../store/appStore", () => ({
  useAppStore: mockUseAppStore,
}));

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
    li: ({ children, ...props }: any) => <li {...props}>{children}</li>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

describe("HistoryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Keep default store contents; only reset call history above.
    mockLocalFetch.mockImplementation((url: string) => {
      if (url === "/health") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: "online",
              ram_used_gb: 2.5,
              ram_total_gb: 8.0,
              cpu_percent: 15.0,
              checks: { database: "ok", audit: "ok", vault: "locked", audit_entries: 42 },
            }),
            { status: 200 }
          )
        );
      }
      if (url === "/stats") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              doc_count: 100,
              active_missions: 3,
              custom_tools: 5,
              credentials: 10,
              browser_sessions: 2,
            }),
            { status: 200 }
          )
        );
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });
  });

  it("renders the System & History heading", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByText(/System & History/)).toBeDefined();
  });

  it("shows Engine Health section", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByText("Engine Health")).toBeDefined();
  });

  it("shows System Checks section", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByText("System Checks")).toBeDefined();
  });

  it("shows Knowledge Vault section", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByText("Knowledge Vault")).toBeDefined();
  });

  it("shows Browser History section", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByText("Browser History")).toBeDefined();
  });

  it("displays health data after loading", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(await screen.findByText("online")).toBeDefined();
    expect(screen.getByText("2.5 / 8.0 GB")).toBeDefined();
    expect(screen.getByText("15.0%")).toBeDefined();
  });

  it("displays stats data after loading", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(await screen.findByText("100")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
  });

  it("shows browsing history entries", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByText("Example")).toBeDefined();
    expect(screen.getByText("Test Site")).toBeDefined();
  });

  it("shows filter input for history", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByPlaceholderText(/filter by url/i)).toBeDefined();
  });

  it("shows Refresh button", async () => {
    const { HistoryPanel } = await import("./HistoryPanel");
    render(<HistoryPanel />);
    expect(screen.getByText("Refresh")).toBeDefined();
  });
});
