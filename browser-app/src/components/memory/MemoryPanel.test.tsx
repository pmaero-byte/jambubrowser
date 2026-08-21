import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock the memory utils
const mockGetProfile = vi.hoisted(() => vi.fn());
const mockUpdateProfile = vi.hoisted(() => vi.fn());
const mockListSessions = vi.hoisted(() => vi.fn());
const mockStoreMemory = vi.hoisted(() => vi.fn());
const mockRecallMemory = vi.hoisted(() => vi.fn());
const mockGetMemoryStats = vi.hoisted(() => vi.fn());

vi.mock("../../utils/memory", () => ({
  getProfile: mockGetProfile,
  updateProfile: mockUpdateProfile,
  listSessions: mockListSessions,
  storeMemory: mockStoreMemory,
  recallMemory: mockRecallMemory,
  getMemoryStats: mockGetMemoryStats,
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

describe("MemoryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetProfile.mockResolvedValue({
      user_id: "default",
      display_name: "Test User",
      interests: ["AI", "Music"],
      expertise: {},
      language: "en",
      work_context: "Research",
      preferences: {},
      created_at: Date.now(),
      updated_at: Date.now(),
    });
    mockListSessions.mockResolvedValue([]);
    mockGetMemoryStats.mockResolvedValue({
      profiles: 1,
      sessions: 0,
      semantic_memories: 5,
      procedural_memories: 2,
    });
  });

  it("renders the Memory heading", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    expect(screen.getByText("Memory")).toBeDefined();
  });

  it("shows profile tab by default", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    expect(screen.getByText("Profile")).toBeDefined();
  });

  it("shows recall tab", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    expect(screen.getByText("Recall")).toBeDefined();
  });

  it("shows sessions tab", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    expect(screen.getByText("Sessions")).toBeDefined();
  });

  it("shows store tab", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    expect(screen.getByText("Store")).toBeDefined();
  });

  it("displays memory stats", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    // Stats arrive asynchronously via getMemoryStats — use findBy*.
    expect(await screen.findByText("Profiles: 1")).toBeDefined();
    expect(screen.getByText("Sessions: 0")).toBeDefined();
    expect(screen.getByText("Semantic: 5")).toBeDefined();
    expect(screen.getByText("Procedural: 2")).toBeDefined();
  });

  it("loads profile data on mount", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    expect(mockGetProfile).toHaveBeenCalledWith("default");
    expect(mockListSessions).toHaveBeenCalledWith("default", 20);
    expect(mockGetMemoryStats).toHaveBeenCalledWith("default");
  });

  it("shows Refresh button", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    expect(screen.getByText("Refresh")).toBeDefined();
  });

  it("switches to recall tab on click", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    await userEvent.click(screen.getByText("Recall"));
    expect(screen.getByPlaceholderText("Search memory…")).toBeDefined();
  });

  it("switches to store tab on click", async () => {
    const { MemoryPanel } = await import("./MemoryPanel");
    render(<MemoryPanel />);
    await userEvent.click(screen.getByText("Store"));
    expect(screen.getByPlaceholderText(/enter a fact/i)).toBeDefined();
  });
});
