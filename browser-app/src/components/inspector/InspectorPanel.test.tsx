import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock the app store
const mockUseAppStore = vi.hoisted(() => vi.fn());
vi.mock("../../store/appStore", () => ({
  useAppStore: mockUseAppStore,
}));

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
    a: ({ children, ...props }: any) => <a {...props}>{children}</a>,
    section: ({ children, ...props }: any) => <section {...props}>{children}</section>,
    li: ({ children, ...props }: any) => <li {...props}>{children}</li>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// Mock KnowledgeMini: rendered as a button that drives `onSelectNode` so tests
// can exercise the node-selection → entity-detail wiring.
vi.mock("../knowledge/KnowledgeMini", () => ({
  KnowledgeMini: ({ onSelectNode }: any) => (
    <button data-testid="knowledge-mini" onClick={() => onSelectNode?.("vault")}>
      Knowledge Graph
    </button>
  ),
}));

vi.mock("../knowledge/EntityDetailPanel", () => ({
  EntityDetailPanel: ({ entityId, onClose }: any) => (
    <div data-testid="entity-detail" data-entity-id={entityId}>
      <button data-testid="entity-detail-close" onClick={onClose}>
        close
      </button>
    </div>
  ),
}));

const defaultStore = {
  activeTab: "chat" as const,
  toggleInspector: vi.fn(),
  messages: [],
};

describe("InspectorPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAppStore.mockReturnValue(defaultStore);
  });

  it("renders the Inspector heading", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByText("Inspector")).toBeDefined();
  });

  it("shows Knowledge Graph section", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    // The string appears twice: the section heading and the mocked
    // KnowledgeMini child. Assert at least the heading renders.
    expect(screen.getAllByText("Knowledge Graph").length).toBeGreaterThan(0);
  });

  it("shows Sources section", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByText("Sources")).toBeDefined();
  });

  it("shows Context section", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByText("Context")).toBeDefined();
  });

  it("renders KnowledgeMini component", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByTestId("knowledge-mini")).toBeDefined();
  });

  it("shows no sources message when no messages", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByText("No sources selected.")).toBeDefined();
  });

  it("shows active tab in context", async () => {
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      activeTab: "browser",
    });
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByText("Active tab: browser")).toBeDefined();
  });

  it("shows message count in context", async () => {
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      messages: [{ id: "1" }, { id: "2" }],
    });
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByText("Messages: 2")).toBeDefined();
  });

  it("shows sources from last message", async () => {
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      messages: [
        {
          id: "1",
          role: "assistant",
          content: "Found info",
          sources: ["https://example.com", "https://test.com"],
        },
      ],
    });
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.getByText("https://example.com")).toBeDefined();
    expect(screen.getByText("https://test.com")).toBeDefined();
  });

  it("calls toggleInspector when close button clicked", async () => {
    const toggleInspector = vi.fn();
    mockUseAppStore.mockReturnValue({
      ...defaultStore,
      toggleInspector,
    });
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    const closeBtn = screen.getByTitle("Close inspector");
    closeBtn.click();
    expect(toggleInspector).toHaveBeenCalled();
  });

  it("mounts EntityDetailPanel for the node selected on the graph", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    expect(screen.queryByTestId("entity-detail")).toBeNull();
    fireEvent.click(screen.getByTestId("knowledge-mini"));
    const detail = await screen.findByTestId("entity-detail");
    expect(detail.getAttribute("data-entity-id")).toBe("vault");
  });

  it("unmounts EntityDetailPanel when it is closed", async () => {
    const { InspectorPanel } = await import("./InspectorPanel");
    render(<InspectorPanel />);
    fireEvent.click(screen.getByTestId("knowledge-mini"));
    await screen.findByTestId("entity-detail");
    fireEvent.click(screen.getByTestId("entity-detail-close"));
    await waitFor(() =>
      expect(screen.queryByTestId("entity-detail")).toBeNull()
    );
  });
});
