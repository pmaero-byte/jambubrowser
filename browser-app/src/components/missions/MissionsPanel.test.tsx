import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock localFetch
const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
}));

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// Mock the results viewer so the panel/child wiring can be asserted without
// hitting /mission/{id}/results.
vi.mock("./MissionResultsViewer", () => ({
  MissionResultsViewer: ({ missionId }: any) => (
    <div data-testid="mission-results" data-mission-id={missionId} />
  ),
}));

describe("MissionsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          missions: [
            {
              id: "1",
              query: "AI research",
              status: "active",
              last_run: Date.now() / 1000,
              next_run: Date.now() / 1000 + 3600,
              schedule: "hourly",
            },
            {
              id: "2",
              query: "Market analysis",
              status: "completed",
              last_run: Date.now() / 1000 - 3600,
              next_run: 0,
              schedule: "none",
            },
          ],
        }),
        { status: 200 }
      )
    );
  });

  it("renders the Missions heading", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    expect(screen.getByText("Missions")).toBeDefined();
  });

  it("displays mission list after loading", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    expect(await screen.findByText("AI research")).toBeDefined();
    expect(screen.getByText("Market analysis")).toBeDefined();
  });

  it("shows mission status", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    expect(await screen.findByText("active")).toBeDefined();
    expect(screen.getByText("completed")).toBeDefined();
  });

  it("shows schedule information", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    expect(await screen.findByText("hourly")).toBeDefined();
  });

  it("shows create mission input", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    expect(screen.getByPlaceholderText(/research topic/i)).toBeDefined();
  });

  it("shows Refresh button", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    expect(screen.getByText("Refresh")).toBeDefined();
  });

  it("shows empty state when no missions", async () => {
    mockLocalFetch.mockResolvedValue(
      new Response(JSON.stringify({ missions: [] }), { status: 200 })
    );
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    expect(await screen.findByText("No missions yet")).toBeDefined();
  });

  it("shows stop button for active missions", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    await screen.findByText("AI research");
    // Stop button should be present for active mission
    const stopButtons = screen.getAllByRole("button");
    expect(stopButtons.length).toBeGreaterThan(0);
  });

  it("mounts MissionResultsViewer for a mission when results are expanded", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    await screen.findByText("AI research");
    expect(screen.queryByTestId("mission-results")).toBeNull();
    fireEvent.click(screen.getByTestId("mission-results-toggle-1"));
    const viewer = await screen.findByTestId("mission-results");
    expect(viewer.getAttribute("data-mission-id")).toBe("1");
  });

  it("unmounts MissionResultsViewer when results are collapsed again", async () => {
    const { MissionsPanel } = await import("./MissionsPanel");
    render(<MissionsPanel />);
    await screen.findByText("AI research");
    fireEvent.click(screen.getByTestId("mission-results-toggle-1"));
    await screen.findByTestId("mission-results");
    fireEvent.click(screen.getByTestId("mission-results-toggle-1"));
    await waitFor(() =>
      expect(screen.queryByTestId("mission-results")).toBeNull()
    );
  });
});
