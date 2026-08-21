import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

// Mock the WebSocket hook so we control telemetry/connection state.
const mockUseAgentWebSocket = vi.hoisted(() => vi.fn());
vi.mock("../../utils/useAgentWebSocket", () => ({
  useAgentWebSocket: mockUseAgentWebSocket,
}));

// Mock the app store so we control privacyMode.
const mockUseAppStore = vi.hoisted(() => vi.fn());
vi.mock("../../store/appStore", () => ({
  useAppStore: mockUseAppStore,
}));

// Mock motion to avoid animation overhead in tests.
vi.mock("motion/react", () => ({
  motion: {
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

describe("StatusBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAppStore.mockReturnValue({ privacyMode: "enhanced" });
  });

  it("shows connected state with telemetry", async () => {
    const { StatusBar } = await import("./StatusBar");
    mockUseAgentWebSocket.mockReturnValue({
      connected: true,
      telemetry: { model: "gemma4", tokens_per_sec: 12.5, cost_usd: 0.0012 },
      agentState: { state: "researching", zone: "web" },
    });
    const { container } = render(<StatusBar />);
    expect(container.textContent).toContain("WS live");
    expect(container.textContent).toContain("gemma4");
    expect(container.textContent).toContain("12.5");
    expect(container.textContent).toContain("0.0012");
    expect(container.textContent).toContain("researching");
    expect(container.textContent).toContain("web");
    expect(container.textContent).toContain("Enhanced");
  });

  it("shows disconnected state", async () => {
    const { StatusBar } = await import("./StatusBar");
    mockUseAgentWebSocket.mockReturnValue({
      connected: false,
      telemetry: null,
      agentState: null,
    });
    const { container } = render(<StatusBar />);
    expect(container.textContent).toContain("WS offline");
    expect(container.textContent).toContain("idle");
    expect(container.textContent).toContain("0.0");
    expect(container.textContent).toContain("0.0000");
  });

  it("shows local_only privacy mode with lock icon", async () => {
    const { StatusBar } = await import("./StatusBar");
    mockUseAppStore.mockReturnValue({ privacyMode: "local_only" });
    mockUseAgentWebSocket.mockReturnValue({
      connected: true,
      telemetry: null,
      agentState: null,
    });
    const { container } = render(<StatusBar />);
    expect(container.textContent).toContain("Local Only");
  });

  it("shows idle state when no agent state", async () => {
    const { StatusBar } = await import("./StatusBar");
    mockUseAgentWebSocket.mockReturnValue({
      connected: true,
      telemetry: null,
      agentState: null,
    });
    const { container } = render(<StatusBar />);
    expect(container.textContent).toContain("idle");
  });
});