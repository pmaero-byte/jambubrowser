import { describe, it, expect } from "vitest";
import { isTauri } from "./api";

describe("api", () => {
  it("isTauri returns false outside Tauri", () => {
    expect(isTauri()).toBe(false);
  });
});
