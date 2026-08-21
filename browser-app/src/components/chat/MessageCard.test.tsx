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

import { MessageCard } from "./MessageCard";

describe("MessageCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders user message with content", async () => {
    const message = { id: "1", role: "user" as const, content: "Hello world" };
    render(<MessageCard message={message} />);
    expect(screen.getByText("Hello world")).toBeDefined();
  });

  it("renders assistant message with content", async () => {
    const message = { id: "1", role: "assistant" as const, content: "Hi there" };
    render(<MessageCard message={message} />);
    expect(screen.getByText("Hi there")).toBeDefined();
  });

  it("shows agent run stats when present", async () => {
    const message = {
      id: "1",
      role: "assistant" as const,
      content: "Done",
      agentRun: {
        total_steps: 5,
        duration_ms: 12000,
        total_cost_usd: 0.0012,
      },
    };
    render(<MessageCard message={message} />);
    expect(screen.getByText("5")).toBeDefined();
    expect(screen.getByText("12.0")).toBeDefined();
    expect(screen.getByText("$0.0012")).toBeDefined();
  });

  it("shows source chips when sources present", async () => {
    const message = {
      id: "1",
      role: "assistant" as const,
      content: "Found info",
      sources: ["https://example.com", "https://test.com"],
    };
    render(<MessageCard message={message} />);
    expect(screen.getByText("example.com")).toBeDefined();
    expect(screen.getByText("test.com")).toBeDefined();
  });

  it("shows expand button when more than 3 sources", async () => {
    const message = {
      id: "1",
      role: "assistant" as const,
      content: "Found info",
      sources: [
        "https://example.com",
        "https://test.com",
        "https://foo.com",
        "https://bar.com",
      ],
    };
    render(<MessageCard message={message} />);
    expect(screen.getByText("+1 more")).toBeDefined();
  });

  it("expands to show all sources when expand clicked", async () => {
    const message = {
      id: "1",
      role: "assistant" as const,
      content: "Found info",
      sources: [
        "https://example.com",
        "https://test.com",
        "https://foo.com",
        "https://bar.com",
      ],
    };
    render(<MessageCard message={message} />);
    await userEvent.click(screen.getByText("+1 more"));
    expect(screen.getByText("bar.com")).toBeDefined();
    expect(screen.getByText("less")).toBeDefined();
  });

  it("shows empty state for assistant message with no content", async () => {
    const message = { id: "1", role: "assistant" as const, content: "" };
    render(<MessageCard message={message} />);
    expect(screen.getByText("…")).toBeDefined();
  });

  it("calls onSourceClick when source chip clicked", async () => {
    const onSourceClick = vi.fn();
    const message = {
      id: "1",
      role: "assistant" as const,
      content: "Found info",
      sources: ["https://example.com"],
    };
    render(<MessageCard message={message} onSourceClick={onSourceClick} />);
    await userEvent.click(screen.getByText("example.com"));
    expect(onSourceClick).toHaveBeenCalledWith("https://example.com");
  });
});
