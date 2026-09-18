import { describe, it, expect, vi } from "vitest";
import { copyPageText } from "./copyPageText";

describe("copyPageText", () => {
  it("unwraps JSON-encoded engine output before copying", async () => {
    const evaluate = vi.fn().mockResolvedValue(JSON.stringify("Hello world"));
    const writeClipboard = vi.fn().mockResolvedValue(undefined);
    const result = await copyPageText("tab-1", evaluate, writeClipboard);
    expect(evaluate).toHaveBeenCalledWith(
      "tab-1",
      "document.body ? document.body.innerText : ''",
    );
    expect(writeClipboard).toHaveBeenCalledWith("Hello world");
    expect(result).toEqual({ chars: 11 });
  });

  it("handles plain text and trims whitespace", async () => {
    const evaluate = vi.fn().mockResolvedValue("  padded  ");
    const writeClipboard = vi.fn().mockResolvedValue(undefined);
    await copyPageText("tab-1", evaluate, writeClipboard);
    expect(writeClipboard).toHaveBeenCalledWith("padded");
  });

  it("propagates clipboard failures", async () => {
    const evaluate = vi.fn().mockResolvedValue("text");
    const writeClipboard = vi.fn().mockRejectedValue(new Error("denied"));
    await expect(copyPageText("tab-1", evaluate, writeClipboard)).rejects.toThrow("denied");
  });
});
