import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock localFetch with a path-aware fake.
const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
  engineOrigin: () => "http://test-engine",
}));

// Mock motion to avoid animation overhead (mirrors MissionsPanel tests).
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

const MONITOR = {
  id: 1,
  url: "https://app.example.com",
  mode: "quick",
  interval_minutes: 60,
  fail_on: "high",
  webhook_url: null,
  enabled: true,
  created_at: 1700000000,
  last_run_at: Date.now() / 1000 - 120,
  last_status: "ok",
  last_finding_count: 3,
  last_error: null,
};

const RUNS = {
  monitor_id: 1,
  count: 2,
  runs: [
    {
      id: 9, monitor_id: 1, run_at: Date.now() / 1000 - 120, status: "ok",
      baseline: false, total_findings: 3, new_findings: 1, resolved_findings: 2,
      by_severity: { high: 1 }, visual_change_pct: 4.2, has_screenshot: true,
      error: null,
    },
    {
      id: 8, monitor_id: 1, run_at: Date.now() / 1000 - 3600, status: "ok",
      baseline: true, total_findings: 2, new_findings: 2, resolved_findings: 0,
      by_severity: { high: 2 }, visual_change_pct: null, has_screenshot: true,
      error: null,
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Path-aware localFetch fake; individual tests override with mockImplementation. */
function defaultFetch(path: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method || "GET").toUpperCase();
  if (path === "/audit/monitors" && method === "GET") {
    return Promise.resolve(jsonResponse({ monitors: [MONITOR], count: 1 }));
  }
  if (path === "/audit/monitors" && method === "POST") {
    return Promise.resolve(jsonResponse({
      monitor: { ...MONITOR, id: 2, url: "https://new.example.com" },
      initial_run: {
        status: "ok", baseline: true, total_findings: 0,
        new_findings: 0, resolved_findings: 0, alerted: false,
      },
    }));
  }
  if (path === "/audit/monitors/1/run") {
    return Promise.resolve(jsonResponse({
      status: "ok", monitor_id: 1, baseline: false, total_findings: 4,
      new_findings: 1, resolved_findings: 0, alerted: true,
      alert_findings: [{ severity: "critical", title: "New XSS" }],
    }));
  }
  if (path === "/audit/monitors/1/runs?limit=10") {
    return Promise.resolve(jsonResponse(RUNS));
  }
  return Promise.resolve(jsonResponse({ status: "ok" }));
}

describe("MonitorsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalFetch.mockImplementation(defaultFetch);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the Monitors heading and explanation", async () => {
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    expect(screen.getByText("Monitors")).toBeDefined();
    expect(screen.getByText(/first run is a baseline/i)).toBeDefined();
  });

  it("lists monitors with URL, interval, and threshold", async () => {
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    expect(await screen.findByText("https://app.example.com")).toBeDefined();
    expect(screen.getByText("every hourly")).toBeDefined();
    expect(screen.getByText("high+")).toBeDefined();
    expect(screen.getByText(/3 findings/)).toBeDefined();
  });

  it("shows the empty state when there are no monitors", async () => {
    mockLocalFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/audit/monitors" && (!init?.method || init.method === "GET")) {
        return Promise.resolve(jsonResponse({ monitors: [], count: 0 }));
      }
      return defaultFetch(path, init);
    });
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    expect(await screen.findByText("No monitors yet")).toBeDefined();
  });

  it("surfaces a load error", async () => {
    mockLocalFetch.mockRejectedValue(new Error("engine down"));
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    expect(await screen.findByRole("alert")).toBeDefined();
  });

  it("creates a monitor with the normalized URL and defaults", async () => {
    const user = userEvent.setup();
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);

    await user.type(screen.getByLabelText("URL to monitor"), "new.example.com");
    await user.click(screen.getByLabelText("Add monitor"));

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith(
        "/audit/monitors",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const createCall = mockLocalFetch.mock.calls.find(
      (c: unknown[]) => c[0] === "/audit/monitors" && (c[1] as RequestInit)?.method === "POST",
    );
    const body = JSON.parse((createCall![1] as RequestInit).body as string);
    expect(body.url).toBe("https://new.example.com");
    expect(body.interval_minutes).toBe(1440);
    expect(body.fail_on).toBe("high");
    expect(body.run_now).toBe(true);
  });

  it("runs a monitor now and shows the diff result", async () => {
    const user = userEvent.setup();
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    await screen.findByText("https://app.example.com");

    await user.click(screen.getByLabelText("Run monitor 1 now"));

    expect(await screen.findByText(/1 new \/ 0 resolved/)).toBeDefined();
    expect(screen.getByText(/alerted/)).toBeDefined();
    expect(mockLocalFetch).toHaveBeenCalledWith(
      "/audit/monitors/1/run",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("toggles enabled state via PATCH", async () => {
    const user = userEvent.setup();
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    await screen.findByText("https://app.example.com");

    await user.click(screen.getByLabelText("Disable monitor 1"));

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith(
        "/audit/monitors/1",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ enabled: false }),
        }),
      );
    });
  });

  it("deletes a monitor", async () => {
    const user = userEvent.setup();
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    await screen.findByText("https://app.example.com");

    await user.click(screen.getByLabelText("Delete monitor 1"));

    await waitFor(() => {
      expect(mockLocalFetch).toHaveBeenCalledWith(
        "/audit/monitors/1",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });

  it("expands run history with baseline and delta badges", async () => {
    const user = userEvent.setup();
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    await screen.findByText("https://app.example.com");

    await user.click(screen.getByLabelText("Toggle run history for monitor 1"));

    expect(await screen.findByText("+1 new")).toBeDefined();
    expect(screen.getByText("−2 resolved")).toBeDefined();
    expect(screen.getByText("visual 4.20%")).toBeDefined();
    expect(screen.getByText("baseline")).toBeDefined();
    expect(mockLocalFetch).toHaveBeenCalledWith("/audit/monitors/1/runs?limit=10");
  });

  it("shows run screenshot thumbnails linking to the PNG endpoint", async () => {
    const user = userEvent.setup();
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    await screen.findByText("https://app.example.com");

    await user.click(screen.getByLabelText("Toggle run history for monitor 1"));

    const thumb = await screen.findByAltText("Screenshot of run 9");
    expect(thumb.getAttribute("src")).toBe(
      "http://test-engine/audit/monitors/1/runs/9/screenshot",
    );
    const link = screen.getByLabelText("Open screenshot for run 9");
    expect(link.getAttribute("href")).toBe(
      "http://test-engine/audit/monitors/1/runs/9/screenshot",
    );
    expect(link.getAttribute("target")).toBe("_blank");
    // Baseline run 8 also stored a screenshot.
    expect(screen.getByAltText("Screenshot of run 8")).toBeDefined();
  });

  it("links the visual diff heatmap only when a comparison exists", async () => {
    const user = userEvent.setup();
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    await screen.findByText("https://app.example.com");

    await user.click(screen.getByLabelText("Toggle run history for monitor 1"));

    // Run 9 has a change percentage → diff link present.
    const diffLink = await screen.findByLabelText("Open visual diff for run 9");
    expect(diffLink.getAttribute("href")).toBe(
      "http://test-engine/audit/monitors/1/runs/9/diff",
    );
    // Run 8 is the baseline (no previous screenshot) → no diff link.
    expect(screen.queryByLabelText("Open visual diff for run 8")).toBeNull();
  });

  it("omits the thumbnail when a run stored no screenshot", async () => {
    const user = userEvent.setup();
    mockLocalFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/audit/monitors/1/runs?limit=10") {
        return Promise.resolve(jsonResponse({
          monitor_id: 1,
          count: 1,
          runs: [{ ...RUNS.runs[0], id: 10, has_screenshot: false }],
        }));
      }
      return defaultFetch(path, init);
    });
    const { MonitorsPanel } = await import("./MonitorsPanel");
    render(<MonitorsPanel />);
    await screen.findByText("https://app.example.com");

    await user.click(screen.getByLabelText("Toggle run history for monitor 1"));

    await screen.findByText("+1 new");
    expect(screen.queryByAltText("Screenshot of run 10")).toBeNull();
  });
});

// ── Pure helpers ─────────────────────────────────────────────────────

describe("monitor helpers", () => {
  it("relativeTime handles never/seconds/minutes/hours/days", async () => {
    const { relativeTime } = await import("./MonitorsPanel");
    const now = Date.now() / 1000;
    expect(relativeTime(null)).toBe("never");
    expect(relativeTime(now - 5)).toBe("just now");
    expect(relativeTime(now - 300)).toBe("5m ago");
    expect(relativeTime(now - 7200)).toBe("2h ago");
    expect(relativeTime(now - 2 * 86400)).toBe("2d ago");
  });

  it("intervalLabel maps known and arbitrary intervals", async () => {
    const { intervalLabel } = await import("./MonitorsPanel");
    expect(intervalLabel(5)).toBe("5 min");
    expect(intervalLabel(1440)).toBe("daily");
    expect(intervalLabel(120)).toBe("2h");
    expect(intervalLabel(90)).toBe("90m");
  });

  it("normalizeMonitorUrl adds scheme and trims", async () => {
    const { normalizeMonitorUrl } = await import("./MonitorsPanel");
    expect(normalizeMonitorUrl("  example.com ")).toBe("https://example.com");
    expect(normalizeMonitorUrl("http://example.com")).toBe("http://example.com");
    expect(normalizeMonitorUrl("")).toBe("");
  });

  it("runScreenshotUrl points at the per-run PNG endpoint", async () => {
    const { runScreenshotUrl } = await import("./MonitorsPanel");
    expect(runScreenshotUrl(1, 9)).toBe(
      "http://test-engine/audit/monitors/1/runs/9/screenshot",
    );
  });

  it("runDiffUrl points at the per-run diff endpoint", async () => {
    const { runDiffUrl } = await import("./MonitorsPanel");
    expect(runDiffUrl(1, 9)).toBe(
      "http://test-engine/audit/monitors/1/runs/9/diff",
    );
  });
});
