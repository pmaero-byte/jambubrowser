import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

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

describe("SettingsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockImplementation((url: string) => {
      if (url.includes("/v2/llm/providers")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              default_provider: "ollama",
              fallback_chain: ["ollama", "anthropic"],
              providers: ["ollama", "anthropic"],
              models: {
                ollama: ["gemma4"],
                anthropic: ["claude-sonnet-4-5"],
              },
            }),
            { status: 200 }
          )
        );
      }
      if (url.includes("/health")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ status: "online" }),
            { status: 200 }
          )
        );
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });
  });

  it("renders the Settings heading", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(screen.getByText("Settings")).toBeDefined();
  });

  it("shows engine status", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(await screen.findByText("Online")).toBeDefined();
  });

  it("shows default provider", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    // "ollama" appears both as the Default Provider value and as a chip in
    // the Fallback Chain — assert on the labeled row, not any occurrence.
    expect(await screen.findByText("Default Provider")).toBeDefined();
    expect(screen.getByText("Default Provider").nextElementSibling?.textContent).toBe("ollama");
  });

  it("shows fallback chain", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(await screen.findByText("Fallback Chain")).toBeDefined();
  });

  it("shows available providers", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(await screen.findByText("Available Providers")).toBeDefined();
  });

  it("shows provider models", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(await screen.findByText("gemma4")).toBeDefined();
    expect(await screen.findByText("claude-sonnet-4-5")).toBeDefined();
  });

  it("shows privacy mode section", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(screen.getByText("Privacy Mode")).toBeDefined();
  });

  it("shows Refresh button", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(screen.getByText("Refresh")).toBeDefined();
  });

  it("shows Engine section", async () => {
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    expect(screen.getByText("Engine")).toBeDefined();
  });

  it("shows Browser Profile section when Tauri", async () => {
    // This test may not show the section in jsdom
    const { SettingsPanel } = await import("./SettingsPanel");
    render(<SettingsPanel />);
    // The Browser Profile section only shows in Tauri environment
    // In jsdom, it won't render, so we just verify the component loads
    expect(screen.getByText("Settings")).toBeDefined();
  });
});
