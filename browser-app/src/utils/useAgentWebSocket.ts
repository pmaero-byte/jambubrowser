import { useSyncExternalStore } from "react";

export interface AgentState {
  state: string;
  zone?: string;
  task_id?: string;
  timestamp: number;
}

export interface AgentTelemetry {
  model: string;
  action: string;
  file_path?: string;
  task_id?: string;
  tokens_generated?: number;
  tokens_per_sec?: number;
  context_size?: number;
  cost_usd?: number;
  timestamp: number;
}

export interface AgentReasoning {
  delta: string;
  task_id?: string;
  timestamp: number;
}

export interface TaskStart {
  task_id: string;
  query: string;
  timestamp: number;
}

export interface TaskEnd {
  task_id: string;
  status: string;
  result_preview?: string;
  tokens_generated?: number;
  tokens_per_sec?: number;
  elapsed_sec?: number;
  timestamp: number;
}

/**
 * Live task — combines start metadata with the most-recent end event (if any).
 * Multiple live tasks can exist simultaneously when the swarm dispatches
 * parallel agents.
 */
export interface LiveTask {
  start: TaskStart;
  end?: TaskEnd;
}

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.hostname}:8001/ws/default`;
}

// ── Shared singleton connection ─────────────────────────────────────────────
// One WebSocket for the whole app. Every useAgentWebSocket() consumer subscribes
// to the same store via useSyncExternalStore — previously each component opened
// its own socket, which meant N parallel connections and status indicators that
// could disagree with each other mid-reconnect.

interface AgentStoreState {
  connected: boolean;
  agentState: AgentState | null;
  telemetry: AgentTelemetry | null;
  reasoning: string;
  liveTasks: Record<string, LiveTask>;
  lastTaskEnd: TaskEnd | null;
}

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
let refCount = 0;

const state: AgentStoreState = {
  connected: false,
  agentState: null,
  telemetry: null,
  reasoning: "",
  liveTasks: {},
  lastTaskEnd: null,
};

const listeners = new Set<() => void>();

/** Version counter bumped on every state change so getSnapshot can be stable. */
let version = 0;

function emit() {
  version += 1;
  for (const fn of listeners) fn();
}

function setConnected(v: boolean) {
  if (state.connected !== v) {
    state.connected = v;
    emit();
  }
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  try {
    ws = new WebSocket(wsUrl());
  } catch {
    reconnectTimer = setTimeout(connect, 3000);
    return;
  }

  ws.onopen = () => setConnected(true);

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      switch (data.type) {
        case "agent.state":
          state.agentState = {
            state: data.state,
            zone: data.zone,
            task_id: data.task_id,
            timestamp: data.timestamp,
          };
          emit();
          break;
        case "agent.telemetry":
          state.telemetry = data as AgentTelemetry;
          emit();
          break;
        case "agent.reasoning":
          state.reasoning += (data as { delta: string }).delta ?? "";
          emit();
          break;
        case "agent.task_start":
          state.liveTasks = {
            ...state.liveTasks,
            [data.task_id]: { start: data },
          };
          state.reasoning = "";
          emit();
          break;
        case "agent.task_end":
          state.lastTaskEnd = data as TaskEnd;
          {
            const existing = state.liveTasks[data.task_id];
            if (existing) {
              state.liveTasks = {
                ...state.liveTasks,
                [data.task_id]: { ...existing, end: data },
              };
            }
          }
          emit();
          break;
      }
    } catch {
      // ignore parse errors
    }
  };

  ws.onclose = () => {
    setConnected(false);
    if (refCount > 0) {
      reconnectTimer = setTimeout(connect, 3000);
    }
  };

  ws.onerror = () => ws?.close();
}

function disconnectIfUnused() {
  // Small delay so tab switches don't tear down the socket we're about to need.
  setTimeout(() => {
    if (refCount === 0 && ws) {
      clearTimeout(reconnectTimer);
      ws.close();
      ws = null;
      setConnected(false);
    }
  }, 1000);
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  refCount += 1;
  connect();
  return () => {
    listeners.delete(fn);
    refCount -= 1;
    disconnectIfUnused();
  };
}

function getSnapshot(): number {
  return version;
}

const clearReasoning = () => {
  state.reasoning = "";
  emit();
};

export function useAgentWebSocket() {
  useSyncExternalStore(subscribe, getSnapshot);

  // Derived values recomputed per render from the shared mutable store.
  const activeTasks = Object.values(state.liveTasks)
    .filter((t) => !t.end)
    .sort((a, b) => b.start.timestamp - a.start.timestamp);
  const currentTask = activeTasks[0]?.start ?? null;

  return {
    connected: state.connected,
    agentState: state.agentState,
    telemetry: state.telemetry,
    reasoning: state.reasoning,
    currentTask,
    activeTasks,
    liveTasks: state.liveTasks,
    lastTaskEnd: state.lastTaskEnd,
    clearReasoning,
  };
}
