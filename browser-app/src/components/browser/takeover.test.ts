import { describe, it, expect, vi, beforeEach } from "vitest";

const localFetchMock = vi.fn();

vi.mock("../../utils/api", () => ({
  localFetch: (...args: unknown[]) => localFetchMock(...args),
}));

import { setAgentTakeover, savedTakeoverSessionId, saveTakeoverSessionId } from "./takeover";

describe("takeover", () => {
  beforeEach(() => {
    localFetchMock.mockReset();
    localStorage.clear();
  });

  it("returns false without a session id and never calls the engine", async () => {
    expect(await setAgentTakeover("", true)).toBe(false);
    expect(localFetchMock).not.toHaveBeenCalled();
  });

  it("posts the takeover flag and reports success", async () => {
    localFetchMock.mockResolvedValue({ ok: true });
    expect(await setAgentTakeover("bs-1", true)).toBe(true);
    const [path, options] = localFetchMock.mock.calls[0];
    expect(path).toBe("/browser/sessions/bs-1/takeover");
    expect((options as RequestInit).method).toBe("POST");
    expect((options as RequestInit).body).toBe(JSON.stringify({ active: true }));
  });

  it("reports failure when the engine call fails", async () => {
    localFetchMock.mockResolvedValue({ ok: false });
    expect(await setAgentTakeover("bs-1", false)).toBe(false);
  });

  it("persists the session id", () => {
    saveTakeoverSessionId("bs-9");
    expect(savedTakeoverSessionId()).toBe("bs-9");
    saveTakeoverSessionId("");
    expect(savedTakeoverSessionId()).toBe("");
  });
});
