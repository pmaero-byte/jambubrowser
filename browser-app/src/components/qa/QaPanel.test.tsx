import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QaPanel } from "./QaPanel";

vi.mock("../../utils/api", () => ({
  localFetch: vi.fn(),
}));

import { localFetch } from "../../utils/api";
const mockFetch = localFetch as ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const OVERVIEW = {
  cases: [
    {
      id: 1,
      name: "login smoke",
      url: "http://localhost:3000",
      kind: "smoke",
      severity: "high",
      enabled: true,
      last_run_at: Date.now() / 1000 - 120,
      stats: {
        runs: 10,
        pass_rate: 0.9,
        heal_rate: 0.05,
        flake_rate: 0.2,
        quarantined: true,
        quarantine_reason: "3 flakes in the last 10 runs",
        consecutive_passes: 0,
        last_status: "flaky",
      },
    },
  ],
  count: 1,
  summary: { cases: 1, quarantined: 1, pass_rate: 0.9, flake_rate: 0.2, heal_rate: 0.05 },
};

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockImplementation((path: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    if (path === "/qa/overview" && method === "GET") {
      return Promise.resolve(jsonResponse(OVERVIEW));
    }
    return Promise.resolve(jsonResponse({ ok: true }));
  });
});

describe("QaPanel", () => {
  it("renders summary cards and case rows", async () => {
    render(<QaPanel />);
    await waitFor(() => {
      // pass/flake rates appear in both the summary card and the case row
      expect(screen.getAllByText("90%").length).toBeGreaterThan(0);
      expect(screen.getAllByText("20%").length).toBeGreaterThan(0);
      expect(screen.getByText(/login smoke/)).toBeTruthy();
      expect(screen.getByText("flaky")).toBeTruthy();
      expect(screen.getAllByText(/quarantined/i).length).toBeGreaterThan(0);
    });
  });

  it("unquarantines a parked case", async () => {
    const user = userEvent.setup();
    render(<QaPanel />);
    await waitFor(() => screen.getByText("Unquarantine"));
    await user.click(screen.getByText("Unquarantine"));
    await waitFor(() => {
      const post = mockFetch.mock.calls.find(
        (c) => c[0] === "/qa/cases/1/unquarantine",
      );
      expect(post).toBeTruthy();
    });
  });

  it("runs a case", async () => {
    const user = userEvent.setup();
    render(<QaPanel />);
    await waitFor(() => screen.getByText("Run"));
    await user.click(screen.getByText("Run"));
    await waitFor(() => {
      const run = mockFetch.mock.calls.find(
        (c) => c[0] === "/qa/cases/1/run" && c[1]?.body,
      );
      expect(run).toBeTruthy();
      expect(JSON.parse(String(run![1].body))).toEqual({ local: true });
    });
  });

  it("shows the empty state", async () => {
    mockFetch.mockImplementation(() =>
      Promise.resolve(
        jsonResponse({ cases: [], count: 0, summary: { cases: 0, quarantined: 0, pass_rate: null, flake_rate: null, heal_rate: null } }),
      ),
    );
    render(<QaPanel />);
    await waitFor(() => {
      expect(screen.getByText(/No QA cases yet/)).toBeTruthy();
    });
  });
});
