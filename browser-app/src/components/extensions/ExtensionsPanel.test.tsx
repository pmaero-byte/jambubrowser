import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

describe("ExtensionsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Ensure a genuinely non-Tauri window. Note: merely defining
    // window.__TAURI__ = undefined still makes `"__TAURI__" in window`
    // true, which sends the component down the Tauri code path.
    const w = window as any;
    delete w.__TAURI__;
  });

  it("renders the Extensions heading", async () => {
    const { ExtensionsPanel } = await import("./ExtensionsPanel");
    render(<ExtensionsPanel />);
    expect(screen.getByText("Extensions")).toBeDefined();
  });

  it("explains the desktop-app requirement in web mode", async () => {
    const { ExtensionsPanel } = await import("./ExtensionsPanel");
    render(<ExtensionsPanel />);
    expect(
      await screen.findByText("Extensions are only available in the desktop app.")
    ).toBeDefined();
  });

  it("shows an entry count of zero once loading settles", async () => {
    const { ExtensionsPanel } = await import("./ExtensionsPanel");
    render(<ExtensionsPanel />);
    // The count badge next to the heading settles at 0 in web mode.
    expect(await screen.findByText("0")).toBeDefined();
  });

  it("shows Refresh button", async () => {
    const { ExtensionsPanel } = await import("./ExtensionsPanel");
    render(<ExtensionsPanel />);
    expect(screen.getByText("Refresh")).toBeDefined();
  });
});
