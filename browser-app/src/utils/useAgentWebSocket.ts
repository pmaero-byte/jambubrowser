import { useEffect, useRef, useState, useCallback } from "react";

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

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.hostname}:8001/ws/default`;
}

export function useAgentWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [agentState, setAgentState] = useState<AgentState | null>(null);
  const [telemetry, setTelemetry] = useState<AgentTelemetry | null>(null);
  const [reasoning, setReasoning] = useState<string>("");
  const [currentTask, setCurrentTask] = useState<TaskStart | null>(null);
  const [lastTaskEnd, setLastTaskEnd] = useState<TaskEnd | null>(null);

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let closed = false;

    function connect() {
      if (closed) return;
      const ws = new WebSocket(wsUrl());
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          switch (data.type) {
            case "agent.state":
              setAgentState({
                state: data.state,
                zone: data.zone,
                task_id: data.task_id,
                timestamp: data.timestamp,
              });
              break;
            case "agent.telemetry":
              setTelemetry(data);
              break;
            case "agent.reasoning":
              setReasoning((prev) => prev + data.delta);
              break;
            case "agent.task_start":
              setCurrentTask(data);
              setReasoning("");
              break;
            case "agent.task_end":
              setLastTaskEnd(data);
              setCurrentTask(null);
              break;
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => ws.close();
    }

    connect();

    return () => {
      closed = true;
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []);

  const clearReasoning = useCallback(() => setReasoning(""), []);

  return {
    connected,
    agentState,
    telemetry,
    reasoning,
    currentTask,
    lastTaskEnd,
    clearReasoning,
  };
}
