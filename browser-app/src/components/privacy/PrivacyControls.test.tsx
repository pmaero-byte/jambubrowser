import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock localFetch so we control API responses.
const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
}));

// Mock the app store.
const mockSetPrivacyMode = vi.fn();
const mockUseAppStore = vi.hoisted(() => vi.fn());
vi.mock("../../store/appStore", () => ({
  useAppStore: mockUseAppStore,
}));

// Mock motion to avoid animation overhead in tests.
vi.mock("motion/react", () => ({
  motion: {
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    button: ({ children, ...props }: any) => <button {...props}>{children}</button>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

describe("PrivacyControls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAppStore.mockReturnValue({ setPrivacyMode: mockSetPrivacyMode });
  });

  it("shows loading state initially", async () => {
    // Return a promise that never resolves to keep loading state.
    mockLocalFetch.mockReturnValue(new Promise(() => {}));
    const { PrivacyControls } = await import("./PrivacyControls");
    const { container } = render(<PrivacyControls />);
    expect(container.textContent).toContain("Loading privacy report");
  });

  it("shows error state when fetch fails", async () => {
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockRejectedValue(new Error("Network error"));

    const { PrivacyControls } = await import("./PrivacyControls");
    render(<PrivacyControls />);
    expect(await screen.findByText("Failed to fetch privacy report")).toBeDefined();
    expect(screen.getByRole("button", { name: /retry/i })).toBeDefined();
  });

  it("renders privacy modes after successful fetch", async () => {
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          privacy: { mode: "enhanced", local_only: false, pii_removal: true, tracking_blocked: true },
          audit: { total_entries: 42, retention_days: 90 },
          vault_status: "locked",
        }),
        { status: 200 }
      )
    );

    const { PrivacyControls } = await import("./PrivacyControls");
    render(<PrivacyControls />);
    expect(await screen.findByText("Privacy Controls")).toBeDefined();
    expect(screen.getByText("Standard")).toBeDefined();
    expect(screen.getByText("Enhanced")).toBeDefined();
    expect(screen.getByText("Maximum")).toBeDefined();
    expect(screen.getAllByText("Local Only").length).toBe(2);
    expect(screen.getByText("Locked")).toBeDefined();
    expect(screen.getByText("Enabled")).toBeDefined();
  });

  it("shows warning confirmation for maximum mode", async () => {
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          privacy: { mode: "enhanced", local_only: false, pii_removal: true, tracking_blocked: true },
          audit: { total_entries: 10, retention_days: 90 },
          vault_status: "locked",
        }),
        { status: 200 }
      )
    );

    const { PrivacyControls } = await import("./PrivacyControls");
    render(<PrivacyControls />);
    // Wait for the report to load
    expect(await screen.findByText("Privacy Controls")).toBeDefined();

    // Click "Maximum" mode — it has a warning, so a confirmation dialog should appear
    const maxBtn = screen.getByText("Maximum");
    await userEvent.click(maxBtn);
    expect(screen.getByText("Confirm")).toBeDefined();
    expect(screen.getByText("Cancel")).toBeDefined();
  });

  it("applies mode after confirmation", async () => {
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          privacy: { mode: "enhanced", local_only: false, pii_removal: true, tracking_blocked: true },
          audit: { total_entries: 10, retention_days: 90 },
          vault_status: "locked",
        }),
        { status: 200 }
      )
    );

    const { PrivacyControls } = await import("./PrivacyControls");
    render(<PrivacyControls />);
    expect(await screen.findByText("Privacy Controls")).toBeDefined();

    // Click Maximum — it has a warning, so confirmation appears
    const maxBtn = screen.getByText("Maximum");
    await userEvent.click(maxBtn);
    expect(screen.getByText("Confirm")).toBeDefined();

    // Click Confirm — should call localFetch to set mode
    await userEvent.click(screen.getByText("Confirm"));
    // After confirm, the pending mode dialog should disappear
    await vi.waitFor(() => {
      expect(screen.queryByText("Confirm")).toBeNull();
    });
  });

  it("cancels mode change when Cancel is clicked", async () => {
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          privacy: { mode: "enhanced", local_only: false, pii_removal: true, tracking_blocked: true },
          audit: { total_entries: 10, retention_days: 90 },
          vault_status: "locked",
        }),
        { status: 200 }
      )
    );

    const { PrivacyControls } = await import("./PrivacyControls");
    render(<PrivacyControls />);
    expect(await screen.findByText("Privacy Controls")).toBeDefined();

    // Click Maximum — confirmation dialog appears
    const maxBtn = screen.getByText("Maximum");
    await userEvent.click(maxBtn);
    expect(screen.getByText("Confirm")).toBeDefined();
    expect(screen.getByText("Cancel")).toBeDefined();

    // Click Cancel — dialog disappears
    await userEvent.click(screen.getByText("Cancel"));
    expect(screen.queryByText("Confirm")).toBeNull();
  });

  it("applies mode directly when no warning", async () => {
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          privacy: { mode: "enhanced", local_only: false, pii_removal: true, tracking_blocked: true },
          audit: { total_entries: 10, retention_days: 90 },
          vault_status: "locked",
        }),
        { status: 200 }
      )
    );

    const { PrivacyControls } = await import("./PrivacyControls");
    render(<PrivacyControls />);
    expect(await screen.findByText("Privacy Controls")).toBeDefined();

    // Click Standard — no warning, should apply directly
    const standardBtn = screen.getByText("Standard");
    await userEvent.click(standardBtn);
    // Should not show confirmation dialog
    expect(screen.queryByText("Confirm")).toBeNull();
  });

  it("shows protection status after successful fetch", async () => {
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          privacy: {
            mode: "enhanced",
            local_only: false,
            pii_removal: true,
            tracking_blocked: true,
            audit_statistics: { pii_detections: 5, blocked_requests: 12 },
          },
          audit: { total_entries: 42, retention_days: 90 },
          vault_status: "locked",
        }),
        { status: 200 }
      )
    );

    const { PrivacyControls } = await import("./PrivacyControls");
    render(<PrivacyControls />);
    expect(await screen.findByText("Privacy Controls")).toBeDefined();

    // Protection status section
    expect(screen.getByText("Protection Status")).toBeDefined();
    expect(screen.getByText("Enabled")).toBeDefined(); // PII Removal
    expect(screen.getByText("5")).toBeDefined(); // PII detections
    expect(screen.getByText("12")).toBeDefined(); // Blocked requests

    // Vault status
    expect(screen.getByText("Locked")).toBeDefined();

    // Audit log
    expect(screen.getByText("42")).toBeDefined(); // Total entries
    expect(screen.getByText("90 days")).toBeDefined();
  });

});