import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

const mockLocalFetch = vi.fn();
vi.mock("../../utils/api", () => ({
  localFetch: (...args: unknown[]) => mockLocalFetch(...args),
}));

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
  };
}

const SAMPLE_RECORDINGS = [
  {
    id: 1,
    name: "login-flow",
    start_url: "https://example.com/login",
    step_count: 3,
    duration_ms: 4200,
    status: "completed",
    error: null,
    created_at: 2461000.0,
  },
  {
    id: 2,
    name: "broken-search",
    start_url: "https://test.com/search",
    step_count: 2,
    duration_ms: 1500,
    status: "failed",
    error: "selector not found",
    created_at: 2461000.0,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

async function renderPanel() {
  const { RecordingsPanel } = await import("./RecordingsPanel");
  return render(<RecordingsPanel />);
}

describe("RecordingsPanel", () => {
  it("shows the empty state when there are no recordings", async () => {
    mockLocalFetch.mockResolvedValue(jsonResponse({ recordings: [] }));
    await renderPanel();
    expect(await screen.findByText("No recordings yet")).toBeDefined();
    expect(screen.getByText(/POST \/sessions\/recordings\/run/)).toBeDefined();
  });

  it("lists recordings with status icons and metadata", async () => {
    mockLocalFetch.mockResolvedValue(jsonResponse({ recordings: SAMPLE_RECORDINGS }));
    await renderPanel();
    expect(await screen.findByText("login-flow")).toBeDefined();
    expect(screen.getByText("broken-search")).toBeDefined();
    expect(screen.getByText(/3 steps/)).toBeDefined();
    // Failed recording shows its error
    expect(screen.getByTitle("selector not found")).toBeDefined();
  });

  it("replays a recording and reports success", async () => {
    mockLocalFetch
      .mockResolvedValueOnce(jsonResponse({ recordings: SAMPLE_RECORDINGS }))
      .mockResolvedValueOnce(jsonResponse({ success: true, replayed_steps: 3 }))
      .mockResolvedValue(jsonResponse({ recordings: SAMPLE_RECORDINGS }));

    await renderPanel();
    fireEvent.click(await screen.findByTitle('Replay "login-flow"'));

    expect(await screen.findByText(/Replayed "login-flow" — 3 steps OK/)).toBeDefined();
    const [path, init] = mockLocalFetch.mock.calls[1];
    expect(path).toBe("/sessions/recordings/1/replay");
    expect(init.method).toBe("POST");
  });

  it("reports a failed replay", async () => {
    mockLocalFetch
      .mockResolvedValueOnce(jsonResponse({ recordings: SAMPLE_RECORDINGS }))
      .mockResolvedValueOnce(jsonResponse({ success: false, error: "timeout at step 2" }, false, 500))
      .mockResolvedValue(jsonResponse({ recordings: SAMPLE_RECORDINGS }));

    await renderPanel();
    fireEvent.click(await screen.findByTitle('Replay "broken-search"'));

    expect(
      await screen.findByText(/Replay of "broken-search" failed: timeout at step 2/)
    ).toBeDefined();
  });

  it("deletes a recording and refreshes", async () => {
    mockLocalFetch
      .mockResolvedValueOnce(jsonResponse({ recordings: SAMPLE_RECORDINGS })) // initial load
      .mockResolvedValueOnce(jsonResponse({ success: true, deleted: 1 }))     // DELETE
      .mockResolvedValue(jsonResponse({ recordings: SAMPLE_RECORDINGS }));    // refresh

    await renderPanel();
    const deleteButtons = await screen.findAllByTitle(/^Delete "/);
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      const call = mockLocalFetch.mock.calls.find((c) => c[1]?.method === "DELETE");
      expect(call?.[0]).toBe("/sessions/recordings/1");
    });
  });
});
