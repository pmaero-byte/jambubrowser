import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

import { AgentTimeline } from "./AgentTimeline";
import type { AgentEvent } from "../../utils/types";

describe("AgentTimeline", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns null when no events and not active", async () => {
    const { container } = render(
      <AgentTimeline events={[]} isActive={false} />
    );
    expect(container.innerHTML).toBe("");
  });

  it("shows 'Agent working' when active", async () => {
    render(<AgentTimeline events={[]} isActive={true} />);
    expect(screen.getByText("Agent working")).toBeDefined();
  });

  it("shows 'Agent timeline' when not active", async () => {
    const events: AgentEvent[] = [
      {
        type: "run_completed",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { total_steps: 3, duration_ms: 5000, total_cost_usd: 0.001 },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} />);
    expect(screen.getByText("Agent timeline")).toBeDefined();
  });

  it("renders run_started event", async () => {
    const events: AgentEvent[] = [
      {
        type: "run_started",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { query: "test query" },
      },
    ];
    render(<AgentTimeline events={events} isActive={true} />);
    expect(screen.getByText("Starting research")).toBeDefined();
    expect(screen.getByText("test query")).toBeDefined();
  });

  it("renders plan_created event", async () => {
    const events: AgentEvent[] = [
      {
        type: "plan_created",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { plan: { steps: [{}, {}, {}] } },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} />);
    expect(screen.getByText("Plan created (3 steps)")).toBeDefined();
  });

  it("renders step_started event", async () => {
    const events: AgentEvent[] = [
      {
        type: "step_started",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { step: { description: "Searching web", tool: "web_search" } },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} />);
    expect(screen.getByText("Searching web")).toBeDefined();
  });

  it("renders tool_called event", async () => {
    const events: AgentEvent[] = [
      {
        type: "tool_called",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { tool: "web_search", result: { duration_ms: 1500, data: { count: 5 } } },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} />);
    expect(screen.getByText("web_search succeeded")).toBeDefined();
    expect(screen.getByText("1500ms · 5 results")).toBeDefined();
  });

  it("renders tool_failed event", async () => {
    const events: AgentEvent[] = [
      {
        type: "tool_failed",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { tool: "scrape_url", error: "Timeout" },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} />);
    expect(screen.getByText("scrape_url failed")).toBeDefined();
    expect(screen.getByText("Timeout")).toBeDefined();
  });

  it("renders run_completed event with stats", async () => {
    const events: AgentEvent[] = [
      {
        type: "run_completed",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { total_steps: 5, duration_ms: 12000, total_cost_usd: 0.0023 },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} />);
    expect(screen.getByText("Run completed")).toBeDefined();
    expect(screen.getByText("5 steps · 12.0s · $0.0023")).toBeDefined();
  });

  it("shows dismiss button when onDismiss provided and not active", async () => {
    const onDismiss = vi.fn();
    const events: AgentEvent[] = [
      {
        type: "run_completed",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { total_steps: 1, duration_ms: 1000, total_cost_usd: 0 },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} onDismiss={onDismiss} />);
    const dismissBtn = screen.getByTitle("Dismiss");
    await userEvent.click(dismissBtn);
    expect(onDismiss).toHaveBeenCalled();
  });

  it("collapses and expands on header click", async () => {
    const events: AgentEvent[] = [
      {
        type: "run_started",
        run_id: "1",
        timestamp: Date.now() / 1000,
        data: { query: "test" },
      },
    ];
    render(<AgentTimeline events={events} isActive={false} />);
    expect(screen.getByText("Starting research")).toBeDefined();
    // Click header to collapse
    await userEvent.click(screen.getByText("Agent timeline"));
    // Events should still be in DOM but collapsed
  });
});
