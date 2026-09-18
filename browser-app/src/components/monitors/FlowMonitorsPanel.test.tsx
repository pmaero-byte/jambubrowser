import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
  engineOrigin: () => "http://test-engine",
}));

vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

const MONITOR = {
  id: 1,
  name: "login smoke",
  url: "http://localhost:3000",
  steps: [{ action: "assert_console_clean" }],
  local: true,
  approve: false,
  network: null,
  interval_minutes: 60,
  webhook_url: null,
  enabled: true,
  created_at: 1700000000,
  last_run_at: Date.now() / 1000 - 120,
  last_status: "passed",
};

const RUNS = {
  monitor_id: 1,
  count: 1,
  runs: [
    {
      id: 5, monitor_id: 1, run_at: Date.now() / 1000 - 120, status: "failed",
      ok: false, passed: 1, failed: 1, total: 2, duration_ms: 300,
      failed_steps: [{ i: 2, action: "click", reason: "target_not_found", error: "no element" }],
      console_errors: [], error: null,
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function defaultFetch(path: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method || "GET").toUpperCase();
  if (path === "/browser/monitors" && method === "GET") {
    return Promise.resolve(jsonResponse({ monitors: [MONITOR], count: 1 }));
  }
  if (path === "/browser/monitors" && method === "POST") {
    return Promise.resolve(jsonResponse({ ...MONITOR, id: 2, name: "new" }));
  }
  if (path === "/browser/monitors/1/run") {
    return Promise.resolve(jsonResponse({ monitor_id: 1, status: "passed", ok: true }));
  }
  if (path === "/browser/monitors/1/runs?limit=10") {
    return Promise.resolve(jsonResponse(RUNS));
  }
  return Promise.resolve(jsonResponse({ status: "ok" }));
}

describe("FlowMonitorsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockImplementation(defaultFetch);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders heading and existing monitors", async () => {
    const { FlowMonitorsPanel } = await import("./FlowMonitorsPanel");
    render(<FlowMonitorsPanel />);
    expect(screen.getByText("Flow tests")).toBeDefined();
    expect(await screen.findByText("login smoke")).toBeDefined();
  });

  it("creates a monitor from the form", async () => {
    const user = userEvent.setup();
    const { FlowMonitorsPanel } = await import("./FlowMonitorsPanel");
    render(<FlowMonitorsPanel />);
    await user.type(screen.getByPlaceholderText(/name/i), "checkout");
    await user.type(screen.getByPlaceholderText(/localhost:3000/), "http://localhost:3000/cart");
    await user.click(screen.getByRole("button", { name: /add/i }));
    await waitFor(() => {
      const posted = mockLocalFetch.mock.calls.find(
        ([p, init]) => p === "/browser/monitors" && (init as RequestInit)?.method === "POST",
      );
      expect(posted).toBeTruthy();
    });
  });

  it("rejects invalid steps JSON", async () => {
    const user = userEvent.setup();
    const { FlowMonitorsPanel } = await import("./FlowMonitorsPanel");
    render(<FlowMonitorsPanel />);
    const boxes = screen.getAllByRole("textbox");
    const stepsBox = boxes.find((b) => (b as HTMLTextAreaElement).tagName === "TEXTAREA");
    expect(stepsBox).toBeTruthy();
    await user.clear(stepsBox!);
    await user.type(stepsBox!, "oops not json");
    await user.type(screen.getByPlaceholderText(/name/i), "bad");
    await user.type(screen.getByPlaceholderText(/localhost:3000/), "http://x");
    await user.click(screen.getByRole("button", { name: /add/i }));
    expect(await screen.findByText(/valid JSON/)).toBeDefined();
    expect(
      mockLocalFetch.mock.calls.some(
        ([p, init]) => p === "/browser/monitors" && (init as RequestInit)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("runs a monitor now and expands its history", async () => {
    const user = userEvent.setup();
    const { FlowMonitorsPanel } = await import("./FlowMonitorsPanel");
    render(<FlowMonitorsPanel />);
    await user.click(await screen.findByTitle("Run now"));
    await waitFor(() => {
      expect(
        mockLocalFetch.mock.calls.some(([p]) => p === "/browser/monitors/1/run"),
      ).toBe(true);
    });
    await user.click(await screen.findByText("login smoke"));
    expect(await screen.findByText(/1\/2 steps/)).toBeDefined();
  });

  it("deletes a monitor", async () => {
    const user = userEvent.setup();
    const { FlowMonitorsPanel } = await import("./FlowMonitorsPanel");
    render(<FlowMonitorsPanel />);
    await screen.findByText("login smoke");
    await user.click(screen.getByTitle("Delete"));
    await waitFor(() => {
      expect(
        mockLocalFetch.mock.calls.some(
          ([p, init]) => p === "/browser/monitors/1" && (init as RequestInit)?.method === "DELETE",
        ),
      ).toBe(true);
    });
  });
});
