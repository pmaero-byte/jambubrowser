import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

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

const ACTIVE_GOAL = {
  id: "g1",
  title: "Audit the pricing page",
  description: "Collect Lighthouse + a11y evidence for /pricing",
  status: "active",
  priority: 2,
  approaches_tried: 4,
  approaches_succeeded: 1,
  success_criteria: ["score >= 90", "no critical a11y issues"],
  constraints: ["no production writes"],
};

const DEFAULT_GOALS = {
  goals: [
    { id: "g1", title: "Audit the pricing page", status: "active", priority: 2, approaches_tried: 4 },
  ],
};

const DEFAULT_APPROACHES = {
  approaches: [
    { id: "a1", iteration: 1, strategy: "Run Lighthouse", result: "falsified", learning: "needs auth" },
  ],
};

/** Route the mocked fetch per endpoint so each call returns its own payload. */
function routeFetch(overrides: Record<string, unknown> = {}) {
  mockLocalFetch.mockImplementation(async (path: string) => {
    const matched = Object.keys(overrides).find((k) => path.startsWith(k));
    const body = matched
      ? overrides[matched]
      : path.startsWith("/goal/list")
        ? DEFAULT_GOALS
        : path.startsWith("/goal/active")
          ? { active: true, goal: ACTIVE_GOAL }
          : path.startsWith("/goal/approaches")
            ? DEFAULT_APPROACHES
            : { status: "goal_set" };
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

const called = (path: string, method?: string) =>
  mockLocalFetch.mock.calls.some(
    ([p, o]: any) => (method ? p === path && o?.method === method : p === path)
  );

describe("GoalsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeFetch();
  });

  it("renders the Goals heading", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    expect(screen.getByText("Goals")).toBeDefined();
  });

  it("loads the goal list and active goal on mount", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    await waitFor(() => {
      expect(screen.getAllByText("Audit the pricing page").length).toBeGreaterThan(0);
    });
    // The active-goal card summarises approach success and criteria counts.
    expect(screen.getByText("1/4 approaches worked")).toBeDefined();
    expect(screen.getByText("2 success criteria")).toBeDefined();
    expect(called("/goal/list")).toBe(true);
    expect(called("/goal/active")).toBe(true);
  });

  it("fetches and renders approaches for the selected goal", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    await waitFor(() => {
      expect(
        mockLocalFetch.mock.calls.some(([p]: any) => p.startsWith("/goal/approaches?goal_id=g1"))
      ).toBe(true);
    });
    expect(screen.getByText("Run Lighthouse")).toBeDefined();
    expect(screen.getByText("falsified")).toBeDefined();
  });

  it("opens the create form and posts a goal with parsed criteria", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    fireEvent.click(screen.getByTitle("New goal"));
    fireEvent.change(screen.getByTestId("goal-title"), { target: { value: "Ship the fix" } });
    fireEvent.change(screen.getByTestId("goal-description"), { target: { value: "and verify" } });
    fireEvent.change(screen.getByTestId("goal-criteria"), {
      target: { value: "tests green\nno regressions\n" },
    });
    fireEvent.click(screen.getByText("Set goal"));

    await waitFor(() => {
      expect(called("/goal/set", "POST")).toBe(true);
    });
    const call = mockLocalFetch.mock.calls.find(([p, o]: any) => p === "/goal/set" && o?.method === "POST");
    const body = JSON.parse((call as any)[1].body);
    expect(body.title).toBe("Ship the fix");
    expect(body.description).toBe("and verify");
    expect(body.success_criteria).toEqual(["tests green", "no regressions"]);
  });

  it("disables Set goal until a title is entered", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    fireEvent.click(screen.getByTitle("New goal"));
    expect((screen.getByText("Set goal") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("goal-title"), { target: { value: "x" } });
    expect((screen.getByText("Set goal") as HTMLButtonElement).disabled).toBe(false);
  });

  it("marks a goal achieved via /goal/achieve", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    await waitFor(() => expect(screen.getByTestId("goal-row-toggle")).toBeDefined());
    fireEvent.click(screen.getByTitle("Mark achieved"));
    await waitFor(() => {
      expect(called("/goal/achieve?goal_id=g1", "POST")).toBe(true);
    });
  });

  it("blocks a goal via /goal/block", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    await waitFor(() => expect(screen.getByTestId("goal-row-toggle")).toBeDefined());
    fireEvent.click(screen.getByTitle("Block"));
    await waitFor(() => {
      expect(called("/goal/block?goal_id=g1", "POST")).toBe(true);
    });
  });

  it("collapses the selected goal row when clicked again", async () => {
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    await waitFor(() => expect(screen.getByText("Run Lighthouse")).toBeDefined());
    fireEvent.click(screen.getByTestId("goal-row-toggle"));
    expect(screen.queryByText("Run Lighthouse")).toBeNull();
  });

  it("surfaces an error banner when the engine is unreachable", async () => {
    mockLocalFetch.mockRejectedValue(new Error("boom"));
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { GoalsPanel } = await import("./GoalsPanel");
    render(<GoalsPanel />);
    await waitFor(() => {
      expect(screen.getByText("Could not reach the goal orchestrator.")).toBeDefined();
    });
    spy.mockRestore();
  });
});