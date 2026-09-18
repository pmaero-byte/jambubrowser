import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

const invokeMock = vi.fn().mockResolvedValue(undefined);

class FakeChannel<T> {
  onmessage: ((message: T) => void) | null = null;
}

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
  Channel: FakeChannel,
}));

import { useScreencast } from "./useScreencast";

describe("useScreencast", () => {
  beforeEach(() => {
    invokeMock.mockClear();
    (window as unknown as Record<string, unknown>).__TAURI__ = {};
  });
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI__;
  });

  it("does nothing outside Tauri", () => {
    delete (window as unknown as Record<string, unknown>).__TAURI__;
    const { result } = renderHook(() => useScreencast("tab-1"));
    expect(result.current.frame).toBeNull();
    expect(result.current.error).toBeNull();
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("starts a screencast and renders incoming frames", async () => {
    const { result } = renderHook(() => useScreencast("tab-1"));

    await waitFor(() => expect(invokeMock).toHaveBeenCalled());
    const call = invokeMock.mock.calls.find((c) => c[0] === "browser_start_screencast");
    expect(call).toBeTruthy();
    const channel = (call![1] as { onFrame: FakeChannel<unknown> }).onFrame;

    act(() => {
      channel.onmessage?.({ kind: "frame", data: "QUJD" });
    });
    expect(result.current.frame).toBe("data:image/jpeg;base64,QUJD");
  });

  it("surfaces stream errors", async () => {
    const { result } = renderHook(() => useScreencast("tab-2"));
    await waitFor(() => expect(invokeMock).toHaveBeenCalled());
    const call = invokeMock.mock.calls.find((c) => c[0] === "browser_start_screencast");
    const channel = (call![1] as { onFrame: FakeChannel<unknown> }).onFrame;

    act(() => {
      channel.onmessage?.({ kind: "error", message: "tab closed" });
    });
    expect(result.current.error).toBe("tab closed");
  });

  it("stops the stream on unmount", async () => {
    const { unmount } = renderHook(() => useScreencast("tab-3"));
    await waitFor(() => expect(invokeMock).toHaveBeenCalled());
    unmount();
    await waitFor(() =>
      expect(invokeMock.mock.calls.some((c) => c[0] === "browser_stop_screencast")).toBe(true),
    );
  });
});
