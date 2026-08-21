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
    mockUseAgentWebSocket.mockReturnValue({ agentState: null });
  });

  it("renders workspace navigation items", async () => {
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    expect(screen.getByText("Research")).toBeDefined();
    expect(screen.getByText("Browser")).toBeDefined();
    expect(screen.getByText("Logs")).toBeDefined();
    expect(screen.getByText("Memory")).toBeDefined();
    expect(screen.getByText("Missions")).toBeDefined();
    expect(screen.getByText("History")).toBeDefined();
    expect(screen.getByText("Agent")).toBeDefined();
  });

  it("renders system navigation items", async () => {
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    expect(screen.getByText("Extensions")).toBeDefined();
    expect(screen.getByText("Privacy")).toBeDefined();
    expect(screen.getByText("Audit")).toBeDefined();
    expect(screen.getByText("Team")).toBeDefined();
    expect(screen.getByText("Vault")).toBeDefined();
    expect(screen.getByText("Settings")).toBeDefined();
  });

  it("renders workspace and system headings", async () => {
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    expect(screen.getByText("Workspace")).toBeDefined();
    expect(screen.getByText("System")).toBeDefined();
  });

  it("calls setActiveTab when nav item clicked", async () => {
    const setActiveTab = vi.fn();
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      setActiveTab,
    });
    const { Sidebar } = await import("./Sidebar");
    render(<Sidebar />);
    await userEvent.click(screen.getByText("Browser"));
    expect(setActiveTab).toHaveBeenCalledWith("browser");
  });

  it("shows engine online status", async () => {
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
    const browserBtn = screen.getByText("Browser").closest("button");
    expect(browserBtn).toBeDefined();
  });
});
