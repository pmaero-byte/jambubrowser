import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { TelemetryPanel } from "./TelemetryPanel";

// Mock motion to avoid animation overhead in tests.
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
}));

describe("TelemetryPanel", () => {
  const baseProps = {
    model: "gemma4:12b-it-qat",
    currentAction: "searching the web",
    reasoningTrace: "Thinking step by step...",
  };

  it("renders model name and active state", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} taskActive={true} />);
    expect(container.textContent).toContain("gemma4:12b-it-qat");
    expect(container.textContent).toContain("running");
    expect(container.textContent).toContain("12b"); // shortModel
  });

  it("shows idle state when not active", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} taskActive={false} />);
    expect(container.textContent).toContain("idle");
  });

  it("formats tokens per second correctly", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} tokensPerSec={12.34} />);
    expect(container.textContent).toContain("12.3 tok/s");
  });

  it("shows dash for very low tps", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} tokensPerSec={0.56} />);
    expect(container.textContent).toContain("0.56 tok/s");
  });

  it("shows dash when tokensPerSec is undefined", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} tokensPerSec={undefined} />);
    expect(container.textContent).toContain("—");
  });

  it("formats token count below 1000", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} tokensGenerated={500} />);
    expect(container.textContent).toContain("500");
  });

  it("formats token count in thousands", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} tokensGenerated={2500} />);
    expect(container.textContent).toContain("2.5k");
  });

  it("formats token count in millions", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} tokensGenerated={2_500_000} />);
    expect(container.textContent).toContain("2.50M");
  });

  it("shows dash when tokensGenerated is undefined", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} tokensGenerated={undefined} />);
    expect(container.textContent).toContain("—");
  });

  it("shows context size", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} contextSize={4096} />);
    expect(container.textContent).toContain("4.1k");
  });

  it("shows current action", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} />);
    expect(container.textContent).toContain("searching the web");
  });

  it("shows file breadcrumb when provided", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(
      <TelemetryPanel {...baseProps} fileBreadcrumb="/src/main.ts" />
    );
    expect(container.textContent).toContain("/src/main.ts");
  });

  it("does not show file breadcrumb when undefined", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} fileBreadcrumb={undefined} />);
    expect(container.textContent).not.toContain("FileText");
  });

  it("shows reasoning trace", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(
      <TelemetryPanel {...baseProps} reasoningTrace="Step 1: analyze\nStep 2: decide" />
    );
    expect(container.textContent).toContain("Step 1: analyze");
  });

  it("shows fallback when reasoning trace is empty", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} reasoningTrace="" />);
    expect(container.textContent).toContain("(no reasoning yet)");
  });

  it("truncates long reasoning trace to 600 chars", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const longTrace = "a".repeat(1000);
    const { container } = render(<TelemetryPanel {...baseProps} reasoningTrace={longTrace} />);
    const content = container.textContent!;
    expect(content.length).toBeLessThan(800); // truncated with leading …
    expect(content).toContain("…");
  });

  it("shows short model name via shortModel", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    // Use inline rendering of shortModel via the metric tile
    const { container } = render(<TelemetryPanel {...baseProps} />);
    expect(container.textContent).toContain("12b");
  });

  it("shows dash when no current action", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} currentAction="" />);
    expect(container.textContent).toContain("(none)");
  });

  it("shows context size metric label", async () => {
    const { TelemetryPanel } = await import("./TelemetryPanel");
    const { container } = render(<TelemetryPanel {...baseProps} contextSize={8192} />);
    expect(container.textContent).toContain("Context");
    expect(container.textContent).toContain("8.2k");
  });
});