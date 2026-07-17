/**
 * Robot sprite generator — 16×16 pixel grid → SVG path strings.
 *
 * Pixel-perfect retro aesthetic. Each cell is 1 unit; the parent SVG uses
 * `shape-rendering="crispEdges"` so every pixel stays sharp at any scale.
 *
 * Color palette is locked to NES/Game Boy inspired tokens (see AgentRoom.css):
 *   --px-bg, --px-bg-2, --px-floor, --px-wood, --px-wood-2,
 *   --px-paper, --px-paper-2, --px-monitor, --px-robot, --px-robot-2,
 *   --px-accent, --px-accent-2, --px-success, --px-error, --px-white, --px-black
 *
 * Sprite anatomy (16×16):
 *   . . . . . . X X X X . . . . . .
 *   . . . . . . X A X A X . . . . .   ← antenna (A = accent yellow)
 *   . . . . . . . X X . . . . . . .
 *   . . . . R R R R R R R R . . . .   ← head
 *   . . . R W R W R W R W R R . . .
 *   . . R W W R W R W R W W R . . .   ← eyes (W = white)
 *   . . R R R R R R R R R R R . . .
 *   . . R W W W W W W W W W R . . .
 *   . . R R R R R R R R R R R . . .
 *   . . . R R R R R R R R R . . . .   ← neck
 *   . . S S S S S S S S S S S . . .   ← body (S = robot shadow)
 *   . . S R R R R R R R R R S . . .
 *   . . S R B R B R B R B R S . . .   ← buttons
 *   . . S R R R R R R R R R S . . .
 *   . . . T T . . . . . T T . . . .   ← wheels/treads
 *   . . . T T . . . . . T T . . . .
 */

export type PixelChar =
  | " " | "." | "X" | "A"   // antenna, accent, accent
  | "R" | "W"               // head body (robot), eye white
  | "S" | "B";              // body shadow, button

export interface SpritePalette {
  /** Primary robot body color */
  robot: string;
  /** Robot body shadow / lower half */
  robotShadow: string;
  /** Eyes, highlights */
  white: string;
  /** Antenna tip, accent details */
  accent: string;
  /** Optional outline (defaults to black) */
  outline: string;
  /** Body button/light color */
  button: string;
}

export interface SpriteOptions {
  /** Blink: close eyes (replaces row 4 W's with R) */
  blink?: boolean;
  /** Squint (for "reading"): narrows eyes to a single W line */
  squint?: boolean;
  /** Walking: shifts whole body 1px left/right */
  walkPhase?: 0 | 1 | 2 | 3;
  /** Antenna pulse brightness (0..1) */
  pulse?: number;
  /** Palette overrides; defaults to robot/accent NES palette */
  palette?: Partial<SpritePalette>;
  /** Sprite state, drives default blink/squint/pulse if explicit overrides not set */
  state?: SpriteState;
}

export type SpriteState =
  | "idle"
  | "walking"
  | "reading"
  | "writing"
  | "thinking"
  | "searching"
  | "error";

/**
 * 16×16 sprite grid. Each char is one pixel. The renderer turns contiguous
 * runs of the same char into SVG `<rect>` elements grouped by color for
 * compact output.
 */
const SPRITE_RAW: string[] = [
  "................",
  "................",
  "................",
  ".......XX.......",
  ".......XAX......",
  "......XXAXX.....",
  ".....RRRRRRRR...",
  "....RWRWRWRWRR..",
  "...RWWRWRWRWWRR.",
  "...RRRRRRRRRRR..",
  "...RWWWWWWWWRR..",
  "...RRRRRRRRRRR..",
  "....RRRRRRRR....",
  "...SSSSSSSSSS...",
  "...SRRSRRRSRRS..",
  "....RR...RR.....",
  "....TT...TT.....",
  "....TT...TT.....",
];

const SPRITE: PixelChar[][] = SPRITE_RAW.map((row) =>
  row.split("") as PixelChar[]
);

const W = SPRITE[0].length;
const H = SPRITE.length;

const COLOR_MAP: Record<PixelChar, keyof SpritePalette | null> = {
  " ": null,
  ".": null,        // transparent background
  "X": "outline",
  "A": "accent",
  "R": "robot",
  "W": "white",
  "S": "robotShadow",
  "B": "button",
};

const DEFAULT_PALETTE: SpritePalette = {
  robot: "var(--px-robot)",
  robotShadow: "var(--px-robot-2)",
  white: "var(--px-white)",
  accent: "var(--px-accent)",
  outline: "var(--px-black)",
  button: "var(--px-accent-2)",
};

/**
 * Run-length encode the sprite into a sequence of {color, x, y, w, h}
 * rectangles. Returns an array of opaque cells.
 */
interface Cell {
  color: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export function renderRobotSprite(opts: SpriteOptions = {}): Cell[] {
  const palette: SpritePalette = { ...DEFAULT_PALETTE, ...(opts.palette || {}) };
  const grid = SPRITE.map((row) => row.slice());

  const state = opts.state ?? "idle";

  // Apply state transforms to the sprite grid.
  // Eyes (rows 7, 8) — blink (idle), squint (reading), or flicker (thinking)
  if (state === "idle" || opts.blink) {
    // Close eyes: W on row 7 → outline; row 8 left intact for "happy" look
    for (let x = 4; x <= 11; x++) {
      if (grid[7]?.[x] === "W") grid[7][x] = "X";
    }
  }
  if (state === "reading" || opts.squint) {
    // Squint: rows 7+8 → narrow to row 8 only with darker R tone
    for (let x = 4; x <= 11; x++) {
      if (grid[7]?.[x] === "W") grid[7][x] = "R";
    }
    for (let x = 4; x <= 11; x++) {
      if (grid[8]?.[x] === "W") grid[8][x] = "R";
    }
  }
  if (state === "thinking") {
    // Eye flicker — alternate colors per call (caller can re-render)
    if (Math.floor(performance.now() / 500) % 2 === 0) {
      for (let x = 5; x <= 10; x++) {
        if (grid[7]?.[x] === "W") grid[7][x] = "A";
      }
    }
  }
  if (state === "searching") {
    // Looking left-right — caller can pass walkPhase to shift eyes
  }
  if (state === "error") {
    // Red X eyes — replace both eye-row whites (rows 7 and 8)
    for (let y = 7; y <= 8; y++) {
      for (let x = 4; x <= 11; x++) {
        if (grid[y]?.[x] === "W") grid[y][x] = "X";
      }
    }
  }
  if (state === "writing") {
    // Add a tiny paper spark above the head: replace row 3 . with paper
    for (let x = 6; x <= 9; x++) {
      if (grid[2]?.[x] === ".") grid[2][x] = "W";
    }
  }

  // Build cells.
  const cells: Cell[] = [];
  for (let y = 0; y < H; y++) {
    let x = 0;
    while (x < W) {
      const ch = grid[y][x];
      const colorKey = COLOR_MAP[ch];
      if (colorKey === null) {
        x++;
        continue;
      }
      // Extend run horizontally.
      let runEnd = x + 1;
      while (runEnd < W && grid[y][runEnd] === ch) runEnd++;
      const color = palette[colorKey];
      if (color) {
        cells.push({ color, x, y, w: runEnd - x, h: 1 });
      }
      x = runEnd;
    }
  }
  return cells;
}

/** Convert a cell list to a single SVG path string. */
export function cellsToSvgPath(cells: Cell[]): string {
  return cells
    .map((c) => `M${c.x},${c.y}h${c.w}v${c.h}h-${c.w}z`)
    .join("");
}

/** Convert to one <rect> element per run (more flexible for per-cell fills). */
export function cellsToSvgRects(cells: Cell[]): string {
  return cells
    .map((c) => {
      const fill = c.color
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
      return `<rect x="${c.x}" y="${c.y}" width="${c.w}" height="${c.h}" fill="${fill}"/>`;
    })
    .join("");
}

export const ROBOT_VIEWBOX = { width: W, height: H };
