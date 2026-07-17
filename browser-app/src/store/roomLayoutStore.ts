/**
 * Room layout preferences store (v2 of Agent Visualization plan).
 *
 * Lets the user move the four zones (desk / cabinet / pile / center) around
 * the 320×200 viewBox by editing coordinates in a settings panel, plus a
 * few cosmetic toggles (sound on/off, multi-agent visible).
 *
 * Persists to localStorage under "jambu-room-layout-v1" so it survives
 * reloads. Invalid stored payloads fall back to defaults silently.
 *
 * Coordinate system: viewBox units (320 wide × 200 tall). 1 unit = 1 SVG
 * pixel; renderer scales it to fit the container.
 */

import { create } from "zustand";

export type ZoneId = "desk" | "cabinet" | "pile" | "center";

export interface ZonePosition {
  x: number;
  y: number;
}

export interface RoomLayout {
  desk: ZonePosition;
  cabinet: ZonePosition;
  pile: ZonePosition;
  center: ZonePosition;
}

export const DEFAULT_LAYOUT: RoomLayout = {
  desk:    { x: 70,  y: 100 },
  cabinet: { x: 250, y: 120 },
  pile:    { x: 165, y: 175 },
  center:  { x: 160, y: 145 },
};

// Soft bounds so users can't drag zones off-stage.
export const ZONE_BOUNDS = {
  minX: 30,
  maxX: 290,
  minY: 50,
  maxY: 180,
};

const STORAGE_KEY = "jambu-room-layout-v1";
const VERSION = 1;

interface PersistedPayload {
  version: number;
  layout: RoomLayout;
  soundEnabled: boolean;
  showMultipleAgents: boolean;
  selectedDeskVariant: number;
}

function loadFromStorage(): Partial<PersistedPayload> | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedPayload;
    if (parsed.version !== VERSION) return null;
    return parsed;
  } catch {
    return null;
  }
}

function saveToStorage(payload: PersistedPayload): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Quota exceeded; we don't try to recover — user can clear later.
  }
}

function clampToBounds(p: ZonePosition): ZonePosition {
  return {
    x: Math.min(Math.max(p.x, ZONE_BOUNDS.minX), ZONE_BOUNDS.maxX),
    y: Math.min(Math.max(p.y, ZONE_BOUNDS.minY), ZONE_BOUNDS.maxY),
  };
}

function isValidLayout(layout: unknown): layout is RoomLayout {
  if (!layout || typeof layout !== "object") return false;
  const l = layout as Record<string, unknown>;
  return (
    isZonePosition(l.desk) &&
    isZonePosition(l.cabinet) &&
    isZonePosition(l.pile) &&
    isZonePosition(l.center)
  );
}

function isZonePosition(v: unknown): v is ZonePosition {
  return (
    !!v &&
    typeof v === "object" &&
    typeof (v as { x: unknown }).x === "number" &&
    typeof (v as { y: unknown }).y === "number"
  );
}

const initial = loadFromStorage();

export interface RoomLayoutState {
  layout: RoomLayout;
  soundEnabled: boolean;
  showMultipleAgents: boolean;
  selectedDeskVariant: number;

  setZonePosition: (zone: ZoneId, p: ZonePosition) => void;
  resetLayout: () => void;
  toggleSound: () => void;
  setSoundEnabled: (v: boolean) => void;
  setShowMultipleAgents: (v: boolean) => void;
  cycleDeskVariant: () => void;
  /** Test helper — wipes the localStorage entry. */
  _clear: () => void;
}

export const useRoomLayoutStore = create<RoomLayoutState>((set, get) => {
  const persist = (next: Partial<PersistedPayload>) => {
    const state = get();
    saveToStorage({
      version: VERSION,
      layout: next.layout ?? state.layout,
      soundEnabled: next.soundEnabled ?? state.soundEnabled,
      showMultipleAgents: next.showMultipleAgents ?? state.showMultipleAgents,
      selectedDeskVariant: next.selectedDeskVariant ?? state.selectedDeskVariant,
    });
  };

  return {
    layout: initial?.layout && isValidLayout(initial.layout) ? initial.layout : DEFAULT_LAYOUT,
    soundEnabled: initial?.soundEnabled ?? true,
    showMultipleAgents: initial?.showMultipleAgents ?? true,
    selectedDeskVariant: initial?.selectedDeskVariant ?? 0,

    setZonePosition: (zone, p) =>
      set((s) => {
        const next: RoomLayout = { ...s.layout, [zone]: clampToBounds(p) };
        persist({ layout: next });
        return { layout: next };
      }),

    resetLayout: () =>
      set(() => {
        persist({ layout: DEFAULT_LAYOUT });
        return { layout: DEFAULT_LAYOUT };
      }),

    toggleSound: () =>
      set((s) => {
        const next = !s.soundEnabled;
        persist({ soundEnabled: next });
        return { soundEnabled: next };
      }),

    setSoundEnabled: (v) =>
      set(() => {
        persist({ soundEnabled: v });
        return { soundEnabled: v };
      }),

    setShowMultipleAgents: (v) =>
      set(() => {
        persist({ showMultipleAgents: v });
        return { showMultipleAgents: v };
      }),

    cycleDeskVariant: () =>
      set((s) => {
        const next = (s.selectedDeskVariant + 1) % DESK_VARIANTS.length;
        persist({ selectedDeskVariant: next });
        return { selectedDeskVariant: next };
      }),

    _clear: () => {
      if (typeof window !== "undefined") {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    },
  };
});

/** Desk variants — purely cosmetic differences in the on-stage SVG. */
export const DESK_VARIANTS = [
  { id: 0, label: "Standing desk", description: "Tall monitor + standing pose" },
  { id: 1, label: "Reading nook",  description: "Lamp + stacked books" },
  { id: 2, label: "Server room",   description: "Rack of blinking lights" },
] as const;
