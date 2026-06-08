import { motion } from "framer-motion";
import { useAgentWebSocket } from "../utils/useAgentWebSocket";

const stateColors: Record<string, string> = {
  idle: "#94a3b8",
  thinking: "#3b82f6",
  searching: "#a855f7",
  reading: "#22c55e",
  writing: "#f59e0b",
  error: "#ef4444",
};

const stateIcons: Record<string, string> = {
  idle: "○",
  thinking: "◉",
  searching: "◎",
  reading: "●",
  writing: "✎",
  error: "✕",
};

export function AgentStatusBar() {
  const { connected, agentState, telemetry, currentTask, lastTaskEnd } = useAgentWebSocket();

  const state = agentState?.state || "idle";
  const color = stateColors[state] || "#94a3b8";
  const icon = stateIcons[state] || "○";

  return (
    <div className="agent-status-bar">
      <div className="status-left">
        <motion.span
          className="status-indicator"
          style={{ color }}
          animate={{ opacity: state !== "idle" ? [0.5, 1, 0.5] : 1 }}
          transition={{ repeat: Infinity, duration: 2 }}
        >
          {connected ? icon : "✖"}
        </motion.span>
        <span className="status-text" style={{ color }}>
          {state === "idle" ? "Ready" : `${state}${agentState?.zone ? ` in ${agentState.zone}` : ""}`}
        </span>
        {currentTask && (
          <span className="task-query" title={currentTask.query}>
            &mdash; {currentTask.query.slice(0, 40)}...
          </span>
        )}
      </div>
      <div className="status-right">
        {telemetry?.tokens_generated && (
          <span className="metric">{telemetry.tokens_generated} tok</span>
        )}
        {telemetry?.tokens_per_sec && (
          <span className="metric">{telemetry.tokens_per_sec.toFixed(1)} t/s</span>
        )}
        {lastTaskEnd?.elapsed_sec && state === "idle" && (
          <span className="metric">{lastTaskEnd.elapsed_sec.toFixed(1)}s</span>
        )}
        <span className={`ws-badge ${connected ? "connected" : "disconnected"}`}>
          {connected ? "WS" : "OFF"}
        </span>
      </div>
    </div>
  );
}
