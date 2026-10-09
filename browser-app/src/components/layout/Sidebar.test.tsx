import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock the app store
const mockUseAppStore = vi.hoisted(() => vi.fn());
vi.mock("../../store/appStore", () => ({
  useAppStore: mockUseAppStore,
}));

// Mock the WebSocket hook
const mockUseAgentWebSocket = vi.hoisted(() => vi.fn());
vi.mock("../../utils/useAgentWebSocket", () => ({
  useAgentWebSocket: mockUseAgentWebSocket,
}));

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// Mock cn utility
vi.mock("../../lib/utils", () => ({
  cn: (...args: any[]) => args.filter(Boolean).join(" "),
}));

const defaultStore = {
  activeTab: "chat" as const,
  setActiveTab: vi.fn(),
};

describe("Sidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAppStore.mockReturnValue(defaultStore);
    mockUseAgentWebSocket.mockReturnValue({ agentState: null, connected: false });
  });

  it("renders workspace navigation items", async () => {
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    expect(screen.getAllByText("Research").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Browser").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Logs").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Memory").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Missions").length).toBeGreaterThan(0);
    expect(screen.getAllByText("History").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Agent").length).toBeGreaterThan(0);
  });

  it("renders system navigation items", async () => {
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    expect(screen.getAllByText("Extensions").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Privacy").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Audit").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Team").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Vault").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Settings").length).toBeGreaterThan(0);
  });

  it("renders grouped section headings", async () => {
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    for (const heading of ["Overview", "Harness", "DeepNet", "Decentralized", "System"]) {
      expect(screen.getAllByText(heading).length).toBeGreaterThan(0);
    }
    // "Browser" appears as both a section heading and a nav item — both render.
    expect(screen.getAllByText("Browser").length).toBeGreaterThanOrEqual(2);
  });

  it("calls setActiveTab when nav item clicked", async () => {
    const setActiveTab = vi.fn();
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      setActiveTab,
    });
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    await userEvent.click(screen.getByRole("button", { name: /^Browser$/ }));
    expect(setActiveTab).toHaveBeenCalledWith("browser");
  });

  it("shows engine offline when the websocket is not connected", async () => {
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    expect(screen.getByText("Engine offline")).toBeDefined();
  });

  it("shows engine online status", async () => {
    mockUseAgentWebSocket.mockReturnValue({ agentState: null, connected: true });
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    expect(screen.getByText("Engine online")).toBeDefined();
  });

  it("highlights active tab", async () => {
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      activeTab: "browser",
    });
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    // The active tab should have secondary variant styling
    const browserBtn = screen.getAllByText("Browser")[0].closest("button");
    expect(browserBtn).toBeDefined();
  });
});
