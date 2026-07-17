/**
 * Sound manager for the Agent Room (v2).
 *
 * Synthesizes tiny chimes via WebAudio so we don't need asset files. Two
 * cues are exposed:
 *
 *   - taskCompleteChime() — a bright C-E-G triad on task_end("completed").
 *   - taskFailedDing()   — a short A3→F3 minor-second drop on task_end("failed").
 *   - typingTick()       — a soft 4 kHz blip, called when reasoning deltas arrive.
 *
 * All sounds respect a single mute flag stored on the room layout store so
 * the user can silence them from the settings popover. The manager is a
 * singleton; we never create more than one AudioContext per page.
 *
 * Browser auto-play policy: AudioContext starts suspended. We resume it on
 * the first user gesture (any of the play methods will resume before
 * scheduling). This is safe — if the user has never interacted, the sounds
 * simply don't play.
 */

type SoundCue = "taskCompleteChime" | "taskFailedDing" | "typingTick";

interface ScheduledNote {
  frequency: number;
  startMs: number;
  durationMs: number;
  gain: number;
  type: OscillatorType;
}

interface Cue {
  notes: ScheduledNote[];
}

const CUES: Record<SoundCue, Cue> = {
  // C5 → E5 → G5, gentle envelope, ~280ms total.
  taskCompleteChime: {
    notes: [
      { frequency: 523.25, startMs: 0,    durationMs: 120, gain: 0.18, type: "triangle" },
      { frequency: 659.25, startMs: 90,   durationMs: 120, gain: 0.16, type: "triangle" },
      { frequency: 783.99, startMs: 180,  durationMs: 140, gain: 0.14, type: "triangle" },
    ],
  },
  // A3 → F3 with a quick drop, sounds like "task failed".
  taskFailedDing: {
    notes: [
      { frequency: 220.0, startMs: 0,   durationMs: 90, gain: 0.22, type: "square" },
      { frequency: 174.61, startMs: 80, durationMs: 200, gain: 0.20, type: "square" },
    ],
  },
  // Short 4 kHz blip, 30ms.
  typingTick: {
    notes: [
      { frequency: 4000, startMs: 0, durationMs: 30, gain: 0.05, type: "sine" },
    ],
  },
};

let _ctx: AudioContext | null = null;
let _lastTickMs = 0;

function getCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (_ctx) return _ctx;
  const Ctor = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  _ctx = new Ctor();
  return _ctx;
}

async function ensureRunning(ctx: AudioContext): Promise<void> {
  if (ctx.state === "running") return;
  try {
    await ctx.resume();
  } catch {
    // Some browsers throw on programmatic resume without a user gesture.
    // We swallow that — the sound will simply be skipped this round.
  }
}

async function playCue(cue: SoundCue, volume = 1): Promise<void> {
  const ctx = getCtx();
  if (!ctx) return;
  await ensureRunning(ctx);
  if (ctx.state !== "running") return;

  const now = ctx.currentTime;
  for (const note of CUES[cue].notes) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = note.type;
    osc.frequency.value = note.frequency;
    const t0 = now + note.startMs / 1000;
    const t1 = t0 + note.durationMs / 1000;
    // Tiny attack/release envelope to avoid clicks.
    gain.gain.setValueAtTime(0, t0);
    gain.gain.linearRampToValueAtTime(note.gain * volume, t0 + 0.01);
    gain.gain.linearRampToValueAtTime(0, t1);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t0);
    osc.stop(t1 + 0.02);
  }
}

/**
 * Public API — call these from React components when WebSocket events fire.
 * Each method early-returns if `enabled` is false (caller passes the
 * roomLayoutStore's soundEnabled flag).
 */

export function playTaskCompleteChime(enabled: boolean): void {
  if (!enabled) return;
  void playCue("taskCompleteChime");
}

export function playTaskFailedDing(enabled: boolean): void {
  if (!enabled) return;
  void playCue("taskFailedDing");
}

/**
 * Typing tick — throttled to one blip per 80ms so a fast reasoning stream
 * doesn't become a buzzsaw.
 */
export function playTypingTick(enabled: boolean): void {
  if (!enabled) return;
  const now = Date.now();
  if (now - _lastTickMs < 80) return;
  _lastTickMs = now;
  void playCue("typingTick", 0.5);
}

/** Test helper — force-close the AudioContext so unit tests stay hermetic. */
export function _resetSoundManagerForTests(): void {
  if (_ctx) {
    try {
      void _ctx.close();
    } catch {
      /* ignore */
    }
  }
  _ctx = null;
  _lastTickMs = 0;
}
