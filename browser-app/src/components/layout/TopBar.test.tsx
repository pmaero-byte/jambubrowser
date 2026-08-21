import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock the app store
const mockUseAppStore = vi.hoisted(() => vi.fn());
vi.mock("../../store/appStore", () => ({
  useAppStore: mockUseAppStore,
}));

describe("TopBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAppStore.mockReturnValue({
      setCommandOpen: vi.fn(),
      privacyMode: "enhanced",
      activeModel: "auto",
    });
  });

  it("renders the Jambubrowser brand name", async () => {
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("Jambubrowser")).toBeDefined();
  });

  it("shows the active model name", async () => {
    mockUseAppStore.mockReturnValue({
      setCommandOpen: vi.fn(),
      privacyMode: "enhanced",
      activeModel: "gemma4",
    });
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("gemma4")).toBeDefined();
  });

  it("shows privacy mode label", async () => {
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("Enhanced")).toBeDefined();
  });

  it("shows Standard privacy mode", async () => {
    mockUseAppStore.mockReturnValue({
      setCommandOpen: vi.fn(),
      privacyMode: "standard",
      activeModel: "auto",
    });
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("Standard")).toBeDefined();
  });

  it("shows Maximum privacy mode", async () => {
    mockUseAppStore.mockReturnValue({
      setCommandOpen: vi.fn(),
      privacyMode: "maximum",
      activeModel: "auto",
    });
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("Maximum")).toBeDefined();
  });

  it("shows Local Only privacy mode", async () => {
    mockUseAppStore.mockReturnValue({
      setCommandOpen: vi.fn(),
      privacyMode: "local_only",
      activeModel: "auto",
    });
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("Local Only")).toBeDefined();
  });

  it("shows Command button with shortcut", async () => {
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("Command")).toBeDefined();
    expect(screen.getByText("⌘K")).toBeDefined();
  });

  it("calls setCommandOpen when Command button clicked", async () => {
    const setCommandOpen = vi.fn();
    mockUseAppStore.mockReturnValue({
      setCommandOpen,
      privacyMode: "enhanced",
      activeModel: "auto",
    });
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    const cmdBtn = screen.getByText("Command").closest("button");
    cmdBtn?.click();
    expect(setCommandOpen).toHaveBeenCalledWith(true);
  });

  it("shows Workspace label", async () => {
    const { TopBar } = await import("./TopBar");
    render(<TopBar />);
    expect(screen.getByText("Workspace")).toBeDefined();
    expect(screen.getByText("Default")).toBeDefined();
  });
});
