/**
 * Tests for the sound manager.
 *
 * We stub `window.AudioContext` with a tiny in-memory mock that records
 * `createOscillator` and `start` calls. That lets us assert the throttling
 * and mute behavior without actually playing sound.
 */

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  playTaskCompleteChime,
  playTaskFailedDing,
  playTypingTick,
  _resetSoundManagerForTests,
} from "./soundManager";

interface MockOscillator {
  type: OscillatorType;
  frequency: { value: number };
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  connect: ReturnType<typeof vi.fn>;
}

function installMockAudioContext() {
  const oscillators: MockOscillator[] = [];

  const makeOsc = () => {
    const o = {
      type: "sine" as OscillatorType,
      frequency: { value: 0 },
      start: vi.fn(),
      stop: vi.fn(),
      connect: vi.fn(() => o),
    } as unknown as MockOscillator;
    oscillators.push(o);
    return o;
  };
  const makeGain = () => ({
    gain: {
      setValueAtTime: vi.fn(),
      linearRampToValueAtTime: vi.fn(),
    },
    connect: vi.fn(),
  });

  const ctx = {
    state: "running" as AudioContextState,
    currentTime: 0,
    resume: vi.fn().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
    createOscillator: vi.fn(makeOsc) as unknown as AudioContext["createOscillator"],
    createGain: vi.fn(makeGain) as unknown as AudioContext["createGain"],
    destination: {} as AudioContext["destination"],
  };

  const AudioContextMock = vi.fn(() => ctx) as unknown as typeof AudioContext;

  Object.defineProperty(window, "AudioContext", {
    configurable: true,
    writable: true,
    value: AudioContextMock,
  });

  return { oscillators, ctx, AudioContextMock };
}

describe("soundManager", () => {
  let mock: ReturnType<typeof installMockAudioContext>;

  beforeEach(() => {
    _resetSoundManagerForTests();
    mock = installMockAudioContext();
  });

  afterEach(() => {
    _resetSoundManagerForTests();
  });

  it("plays the chime when enabled", async () => {
    playTaskCompleteChime(true);
    await Promise.resolve();
    await Promise.resolve();
    // Chime = C5 + E5 + G5 = 3 notes.
    expect(mock.oscillators.length).toBe(3);
  });

  it("is silent when disabled", () => {
    playTaskCompleteChime(false);
    expect(mock.oscillators.length).toBe(0);
  });

  it("plays the failure ding when enabled", async () => {
    playTaskFailedDing(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(mock.oscillators.length).toBe(2);
  });

  it("throttles typing ticks to ~80ms", async () => {
    playTypingTick(true);
    playTypingTick(true);
    playTypingTick(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(mock.oscillators.length).toBe(1);
  });

  it("plays the typing tick after the throttle window", async () => {
    playTypingTick(true);
    await new Promise((r) => setTimeout(r, 100));
    playTypingTick(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(mock.oscillators.length).toBe(2);
  });
});
