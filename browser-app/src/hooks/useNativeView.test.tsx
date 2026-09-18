import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { useRef } from "react";
import type { RefObject } from "react";

const invokeMock = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { useNativeView } from "./useNativeView";

const RECT = {
  x: 10, y: 20, width: 800, height: 600,
  top: 20, left: 10, bottom: 620, right: 810,
  toJSON() { return {}; },
} as unknown as DOMRect;

function Harness({ tabId, url, enabled }: { tabId?: string; url?: string; enabled: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const state = useNativeView(tabId, url, ref as RefObject<HTMLElement | null>, enabled);
  return (
    <div>
      <div ref={ref} data-testid="slot" />
      <span data-testid="live">{state.liveUrl ?? "none"}</span>
      <span data-testid="err">{state.error ?? "ok"}</span>
    </div>
  );
}

describe("useNativeView", () => {
  beforeEach(() => {
    invokeMock.mockReset();
    invokeMock.mockResolvedValue("http://example.com/");
    (window as unknown as Record<string, unknown>).__TAURI__ = {};
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(RECT);
  });

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI__;
    vi.restoreAllMocks();
  });

  it("does nothing outside Tauri", () => {
    delete (window as unknown as Record<string, unknown>).__TAURI__;
    render(<Harness tabId="t1" url="http://example.com/" enabled />);
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("shows the child view at the container rect", async () => {
    render(<Harness tabId="t1" url="http://example.com/" enabled />);
    await waitFor(() => expect(invokeMock).toHaveBeenCalled());
    const call = invokeMock.mock.calls.find((c) => c[0] === "browser_native_view");
    expect(call).toBeTruthy();
    expect(call![1]).toMatchObject({
      tabId: "t1", url: "http://example.com/",
      x: 10, y: 20, width: 800, height: 600,
    });
  });

  it("navigates the existing child when the URL changes", async () => {
    const { rerender } = render(<Harness tabId="t1" url="http://a.example/" enabled />);
    await waitFor(() => expect(invokeMock).toHaveBeenCalled());
    invokeMock.mockClear();
    rerender(<Harness tabId="t1" url="http://b.example/" enabled />);
    await waitFor(() => {
      const call = invokeMock.mock.calls.find((c) => c[0] === "browser_native_view");
      expect(call?.[1]).toMatchObject({ url: "http://b.example/" });
    });
    expect(
      invokeMock.mock.calls.some((c) => c[0] === "browser_native_close"),
    ).toBe(false);
  });

  it("closes the child on unmount and surfaces start errors", async () => {
    invokeMock.mockRejectedValueOnce(new Error("no engine"));
    const { unmount, getByTestId } = render(
      <Harness tabId="t9" url="http://example.com/" enabled />,
    );
    await waitFor(() => expect(getByTestId("err").textContent).toBe("no engine"));
    unmount();
    await waitFor(() => {
      expect(
        invokeMock.mock.calls.some((c) => c[0] === "browser_native_close"),
      ).toBe(true);
    });
  });
});
