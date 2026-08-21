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

// Mock cmdk
vi.mock("cmdk", () => ({
  CommandDialog: ({ children, open, ...props }: any) =>
    open ? <div role="dialog" {...props}>{children}</div> : null,
  CommandInput: (props: any) => <input {...props} />,
  CommandList: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  CommandEmpty: ({ children }: any) => <div>{children}</div>,
  CommandGroup: ({ children, heading }: any) => (
    <div>
      <div role="heading">{heading}</div>
      {children}
    </div>
  ),
  CommandItem: ({ children, onSelect, ...props }: any) => (
    <button onClick={onSelect} {...props}>{children}</button>
  ),
  CommandSeparator: () => <hr />,
}));

const defaultStore = {
  commandOpen: true,
  setCommandOpen: vi.fn(),
  setActiveTab: vi.fn(),
  addBrowserTab: vi.fn(),
  setOnboardingOpen: vi.fn(),
  toggleSidebar: vi.fn(),
  toggleInspector: vi.fn(),
};

describe("CommandPalette", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAppStore.mockReturnValue(defaultStore);
    mockUseAgentWebSocket.mockReturnValue({ clearReasoning: vi.fn() });
  });

  it("renders when commandOpen is true", async () => {
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    expect(screen.getByRole("dialog")).toBeDefined();
  });

  it("does not render when commandOpen is false", async () => {
    mockUseAppStore.mockReturnValue({ ...defaultStore, commandOpen: false });
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders navigation items", async () => {
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    expect(screen.getByText("Research Chat")).toBeDefined();
    expect(screen.getByText("Browser")).toBeDefined();
    expect(screen.getByText("Logs / Audit")).toBeDefined();
    expect(screen.getByText("Memory")).toBeDefined();
  });

  it("renders action items", async () => {
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    expect(screen.getByText("Toggle sidebar")).toBeDefined();
    expect(screen.getByText("Toggle inspector")).toBeDefined();
    expect(screen.getByText("Clear reasoning trace")).toBeDefined();
  });

  it("renders preference items", async () => {
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    expect(screen.getByText("Toggle dark mode")).toBeDefined();
    expect(screen.getByText("Lock vault")).toBeDefined();
    expect(screen.getByText("Change privacy mode")).toBeDefined();
  });

  it("calls setActiveTab when navigation item clicked", async () => {
    const setActiveTab = vi.fn();
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      setActiveTab,
    });
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    await userEvent.click(screen.getByText("Memory"));
    expect(setActiveTab).toHaveBeenCalledWith("memory");
    expect(defaultStore.setCommandOpen).toHaveBeenCalledWith(false);
  });

  it("calls addBrowserTab when Browser item clicked", async () => {
    const addBrowserTab = vi.fn();
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      addBrowserTab,
    });
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    await userEvent.click(screen.getByText("Browser"));
    expect(addBrowserTab).toHaveBeenCalled();
    expect(defaultStore.setCommandOpen).toHaveBeenCalledWith(false);
  });

  it("calls toggleSidebar when Toggle sidebar clicked", async () => {
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    await userEvent.click(screen.getByText("Toggle sidebar"));
    expect(defaultStore.toggleSidebar).toHaveBeenCalled();
    expect(defaultStore.setCommandOpen).toHaveBeenCalledWith(false);
  });

  it("renders search input", async () => {
    const { CommandPalette } = await import("./CommandPalette");
    render(<CommandPalette />);
    expect(screen.getByPlaceholderText("Type a command or search...")).toBeDefined();
  });
});
