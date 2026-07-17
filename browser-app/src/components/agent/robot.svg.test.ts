/**
 * Tests for the robot sprite generator.
 *
 * Verifies the renderer produces a stable shape across states and that the
 * pixel-to-color mapping is preserved.
 */

import { describe, it, expect } from "vitest";
import {
  renderRobotSprite,
  cellsToSvgRects,
  ROBOT_VIEWBOX,
  type SpriteState,
} from "./robot.svg";

describe("renderRobotSprite", () => {
  it("returns cells within the 16x16 viewBox for every state", () => {
    const states: SpriteState[] = [
      "idle",
      "walking",
      "reading",
      "writing",
      "thinking",
      "searching",
      "error",
    ];
    for (const state of states) {
      const cells = renderRobotSprite({ state });
      expect(cells.length, `state=${state}`).toBeGreaterThan(0);
      for (const cell of cells) {
        expect(cell.x, `state=${state} cell.x`).toBeGreaterThanOrEqual(0);
        expect(cell.y, `state=${state} cell.y`).toBeGreaterThanOrEqual(0);
        expect(cell.x + cell.w, `state=${state} cell.x+cell.w`).toBeLessThanOrEqual(
          ROBOT_VIEWBOX.width
        );
        expect(cell.y + cell.h, `state=${state} cell.y+cell.h`).toBeLessThanOrEqual(
          ROBOT_VIEWBOX.height
        );
      }
    }
  });

  it("defaults to idle state when no state given", () => {
    const a = renderRobotSprite();
    const b = renderRobotSprite({ state: "idle" });
    expect(a).toEqual(b);
  });

  it("reading state hides the eye whites (squint)", () => {
    const reading = renderRobotSprite({ state: "reading" });
    const walking = renderRobotSprite({ state: "walking" });
    // The eye-row should contain no white pixels in reading state.
    const eyeRowY = 7;
    const hasWhiteReading = reading.some(
      (c) => c.y === eyeRowY && c.color.includes("white")
    );
    const hasWhiteWalking = walking.some(
      (c) => c.y === eyeRowY && c.color.includes("white")
    );
    expect(hasWhiteReading).toBe(false);
    expect(hasWhiteWalking).toBe(true);
  });

  it("error state removes eye whites", () => {
    const error = renderRobotSprite({ state: "error" });
    const eyeRowY = 7;
    const hasWhite = error.some(
      (c) => c.y === eyeRowY && c.color.includes("white")
    );
    expect(hasWhite).toBe(false);
  });
});

describe("cellsToSvgRects", () => {
  it("produces one rect per cell with valid SVG attributes", () => {
    const cells = renderRobotSprite({ state: "idle" });
    const svg = cellsToSvgRects(cells);
    expect(svg).toMatch(/^<rect /);
    expect(svg).toMatch(/fill="[^"]+"/);
    expect(svg).toMatch(/width="\d+"/);
    expect(svg).toMatch(/height="\d+"/);
    // One <rect> element per cell.
    const rectCount = (svg.match(/<rect /g) || []).length;
    expect(rectCount).toBe(cells.length);
  });

  it("escapes attribute values correctly (no injected markup)", () => {
    const evil = renderRobotSprite({
      state: "idle",
      palette: { white: '"><script>alert(1)</script>' },
    });
    const svg = cellsToSvgRects(evil);
    // The injected characters must be escaped, not passed through.
    expect(svg).not.toContain('<script>');
    expect(svg).toContain("&lt;script&gt;");
    expect(svg).toContain("&quot;");
  });
});
