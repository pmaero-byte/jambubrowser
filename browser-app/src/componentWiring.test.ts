import { describe, it, expect } from "vitest";

/**
 * Regression guard against "built but never mounted" components.
 *
 * Three panels (EntityDetailPanel, MissionResultsViewer, MoaPresetsEditor) were
 * shipped complete, compiling and unit-tested, but referenced by nothing -- so
 * they were dead weight invisible to tsc, lint, and the test suite alike. This
 * guard makes that class of drift a build failure: every component under
 * src/components must be pulled in by at least one *production* source, via
 * either a static `import ... from "./X"` or a lazy `import("./X")` (the
 * pattern App.tsx uses for code-split panels).
 *
 * References from `*.test.tsx` files deliberately do not count -- a component
 * exercised only by its own test is exactly the failure mode being guarded.
 */

/** Every non-test source file, keyed by a path relative to this file. */
// NOTE: both arguments must be inline literals -- Vite's glob transform parses
// them statically and rejects identifiers.
const productionSources = import.meta.glob(
  [
    "./**/*.ts",
    "./**/*.tsx",
    "!./**/*.test.ts",
    "!./**/*.test.tsx",
    "!./**/*.d.ts",
  ],
  { query: "?raw", import: "default", eager: true }
) as Record<string, string>;

/** Components expected to be mounted somewhere. Keys match the above. */
const componentFiles = Object.keys(productionSources).filter(
  (key) =>
    key.startsWith("./components/") && key.endsWith(".tsx") && !key.endsWith("/index.tsx")
);

/** True if some production source other than `self` imports/re-exports `name`. */
function isImported(self: string, name: string): boolean {
  // Matches `from "./a/b/Name"`, `from "../Name"`, and lazy `import("./a/Name")`.
  const specifier = new RegExp(`(?:from|import)\\s*\\(?\\s*["'][^"']*\\/${name}["']`);
  for (const [file, text] of Object.entries(productionSources)) {
    if (file === self) continue;
    if (specifier.test(text)) return true;
  }
  return false;
}

describe("component wiring", () => {
  it("discovers the component tree to check", () => {
    // Guards the guard: if discovery breaks, the loop below would pass
    // vacuously over an empty list.
    expect(componentFiles.length).toBeGreaterThan(20);
  });

  for (const file of componentFiles) {
    const name = file.slice(file.lastIndexOf("/") + 1, -".tsx".length);
    it(`${file} is mounted by a production source`, () => {
      expect(
        isImported(file, name),
        `${file} defines "${name}" but nothing imports it. It is dead code: ` +
          `wire it into a host panel/App route, or delete it.`
      ).toBe(true);
    });
  }
});