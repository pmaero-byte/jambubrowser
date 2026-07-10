import { create } from "zustand";

// ── Types ───────────────────────────────────────────────────────────────────

export interface DevtoolsResource {
  name: string;
  initiatorType: string;
  startTime: number;
  duration: number;
  dnsStart: number;
  dnsEnd: number;
  connectStart: number;
  connectEnd: number;
  ttfb: number;
  responseStart: number;
  responseEnd: number;
  transferSize: number;
  encodedBodySize: number;
  decodedBodySize: number;
  nextHopProtocol: string;
}

export interface DevtoolsNavigation {
  url: string;
  domContentLoaded: number;
  load: number;
  domInteractive: number;
  ttfb: number;
  dnsTime: number;
  tcpTime: number;
  transferSize: number;
  decodedBodySize: number;
  type: string;
}

export interface DevtoolsLCP {
  renderTime: number;
  loadTime: number;
  size: number;
  id: string;
  url: string;
}

export interface DevtoolsFCP {
  startTime: number;
}

export interface DevtoolsCLS {
  value: number;
  sources: number;
  hadRecentInput: boolean;
}

export interface DevtoolsLongTask {
  duration: number;
  startTime: number;
  name: string;
}

export interface DevtoolsConsoleEntry {
  level: "log" | "info" | "warn" | "error" | "debug";
  message: string;
  timestamp: number;
}

export interface DevtoolsErrorEntry {
  message: string;
  filename?: string;
  lineno?: number;
  colno?: number;
  source?: string;
  timestamp?: number;
}

export interface PerfSummary {
  lcp: DevtoolsLCP | null;
  fcp: DevtoolsFCP | null;
  cls: DevtoolsCLS | null;
  navigation: DevtoolsNavigation | null;
  totalResources: number;
  totalTransferSize: number;
  totalDuration: number;
}

// ── Store ───────────────────────────────────────────────────────────────────

interface DevtoolsState {
  /** Whether the devtools panel is open */
  devtoolsOpen: boolean;

  /** Active tab: "network" | "console" | "performance" */
  activeTab: "network" | "console" | "performance";

  /** Network resources (ring buffer: keep last 200) */
  resources: DevtoolsResource[];

  /** Console entries (ring buffer: keep last 500) */
  consoleEntries: DevtoolsConsoleEntry[];

  /** JS errors (ring buffer: keep last 100) */
  errors: DevtoolsErrorEntry[];

  /** Core Web Vitals */
  lcp: DevtoolsLCP | null;
  fcp: DevtoolsFCP | null;
  cls: DevtoolsCLS | null;

  /** Long tasks */
  longTasks: DevtoolsLongTask[];

  /** Navigation timing */
  navigation: DevtoolsNavigation | null;

  /** Perf summary (computed) */
  perfSummary: PerfSummary;

  // Actions
  setDevtoolsOpen: (open: boolean) => void;
  setActiveTab: (tab: "network" | "console" | "performance") => void;

  addResource: (res: DevtoolsResource) => void;
  addConsoleEntry: (entry: DevtoolsConsoleEntry) => void;
  addError: (err: DevtoolsErrorEntry) => void;
  setLcp: (lcp: DevtoolsLCP) => void;
  setFcp: (fcp: DevtoolsFCP) => void;
  setCls: (cls: DevtoolsCLS) => void;
  addLongTask: (task: DevtoolsLongTask) => void;
  setNavigation: (nav: DevtoolsNavigation) => void;

  clearAll: () => void;
}

const MAX_RESOURCES = 200;
const MAX_CONSOLE = 500;
const MAX_ERRORS = 100;
const MAX_LONG_TASKS = 50;

function computePerfSummary(state: {
  lcp: DevtoolsLCP | null;
  fcp: DevtoolsFCP | null;
  cls: DevtoolsCLS | null;
  navigation: DevtoolsNavigation | null;
  resources: DevtoolsResource[];
}): PerfSummary {
  const totalTransferSize = state.resources.reduce((s, r) => s + (r.transferSize || 0), 0);
  const totalDuration = state.resources.reduce((s, r) => s + (r.duration || 0), 0);
  return {
    lcp: state.lcp,
    fcp: state.fcp,
    cls: state.cls,
    navigation: state.navigation,
    totalResources: state.resources.length,
    totalTransferSize,
    totalDuration,
  };
}

function ringAppend<T>(arr: T[], item: T, max: number): T[] {
  const next = [...arr, item];
  if (next.length > max) next.splice(0, next.length - max);
  return next;
}

export const useDevtoolsStore = create<DevtoolsState>((set) => ({
  devtoolsOpen: false,
  activeTab: "network",

  resources: [],
  consoleEntries: [],
  errors: [],
  lcp: null,
  fcp: null,
  cls: null,
  longTasks: [],
  navigation: null,
  perfSummary: {
    lcp: null,
    fcp: null,
    cls: null,
    navigation: null,
    totalResources: 0,
    totalTransferSize: 0,
    totalDuration: 0,
  },

  setDevtoolsOpen: (open) => set({ devtoolsOpen: open }),
  setActiveTab: (tab) => set({ activeTab: tab }),

  addResource: (res) =>
    set((s) => {
      const resources = ringAppend(s.resources, res, MAX_RESOURCES);
      const perfSummary = computePerfSummary({ ...s, resources });
      return { resources, perfSummary };
    }),

  addConsoleEntry: (entry) =>
    set((s) => ({
      consoleEntries: ringAppend(s.consoleEntries, entry, MAX_CONSOLE),
    })),

  addError: (err) =>
    set((s) => ({
      errors: ringAppend(s.errors, err, MAX_ERRORS),
    })),

  setLcp: (lcp) =>
    set((s) => {
      const perfSummary = computePerfSummary({ ...s, lcp });
      return { lcp, perfSummary };
    }),

  setFcp: (fcp) =>
    set((s) => {
      const perfSummary = computePerfSummary({ ...s, fcp });
      return { fcp, perfSummary };
    }),

  setCls: (cls) =>
    set((s) => {
      const perfSummary = computePerfSummary({ ...s, cls });
      return { cls, perfSummary };
    }),

  addLongTask: (task) =>
    set((s) => ({
      longTasks: ringAppend(s.longTasks, task, MAX_LONG_TASKS),
    })),

  setNavigation: (nav) =>
    set((s) => {
      const perfSummary = computePerfSummary({ ...s, navigation: nav });
      return { navigation: nav, perfSummary };
    }),

  clearAll: () =>
    set({
      resources: [],
      consoleEntries: [],
      errors: [],
      lcp: null,
      fcp: null,
      cls: null,
      longTasks: [],
      navigation: null,
      perfSummary: {
        lcp: null,
        fcp: null,
        cls: null,
        navigation: null,
        totalResources: 0,
        totalTransferSize: 0,
        totalDuration: 0,
      },
    }),
}));

// ── Helper: format bytes ────────────────────────────────────────────────────

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatMs(ms: number): string {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return `${ms.toFixed(0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}
