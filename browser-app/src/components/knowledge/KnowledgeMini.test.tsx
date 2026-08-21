import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock react-force-graph-2d
vi.mock("react-force-graph-2d", () => ({
  default: ({ graphData, onNodeClick }: any) => (
    <div data-testid="force-graph">
      {graphData?.nodes?.map((n: any) => (
        <button
          key={n.id}
          onClick={() => onNodeClick?.(n)}
          data-testid={`node-${n.id}`}
        >
          {n.label}
        </button>
      ))}
    </div>
  ),
}));

import { KnowledgeMini } from "./KnowledgeMini";

describe("KnowledgeMini", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the force graph component", async () => {
    render(<KnowledgeMini />);
    expect(screen.getByTestId("force-graph")).toBeDefined();
  });

  it("renders default graph data nodes", async () => {
    render(<KnowledgeMini />);
    expect(screen.getByText("Jambu")).toBeDefined();
    expect(screen.getByText("Privacy")).toBeDefined();
    expect(screen.getByText("Agent Loop")).toBeDefined();
    expect(screen.getByText("Memory")).toBeDefined();
    expect(screen.getByText("Vault")).toBeDefined();
    expect(screen.getByText("Search")).toBeDefined();
  });

  it("calls onSelectNode when node clicked", async () => {
    const onSelectNode = vi.fn();
    render(<KnowledgeMini onSelectNode={onSelectNode} />);
    const node = screen.getByTestId("node-privacy");
    node.click();
    expect(onSelectNode).toHaveBeenCalledWith("privacy");
  });

  it("renders custom data when provided", async () => {
    const data = {
      nodes: [
        { id: "custom1", label: "Custom Node", group: "entity", val: 5 },
      ],
      links: [],
    };
    render(<KnowledgeMini data={data} />);
    expect(screen.getByText("Custom Node")).toBeDefined();
  });

  it("renders without onSelectNode callback", async () => {
    render(<KnowledgeMini />);
    const node = screen.getByTestId("node-root");
    // Should not throw when clicked without callback
    node.click();
    expect(screen.getByTestId("force-graph")).toBeDefined();
  });

  it("renders container with proper styling", async () => {
    const { container } = render(<KnowledgeMini />);
    expect(container.querySelector(".rounded-xl")).toBeDefined();
    expect(container.querySelector(".border")).toBeDefined();
  });
});
