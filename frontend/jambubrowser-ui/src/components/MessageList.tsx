import React from "react";
import { motion } from "framer-motion";

/**
 * Premium Message List
 * --------------------
 * Renders the flow of conversation.
 * Handles 'Deep Trust' source highlighting.
 * Optionally renders an AgentTimeline above the latest assistant message
 * while the agent is working or has just finished.
 */

interface Message {
  role: string;
  content: string;
  sources?: string[];
  agentRun?: {
    total_steps?: number;
    duration_ms?: number;
    total_cost_usd?: number;
  };
}

interface MessageListProps {
  messages: Message[];
  onSourceClick: (url: string) => void;
  /** Optional timeline element rendered above the latest message */
  agentTimeline?: React.ReactNode;
}

export const MessageList = ({ messages, onSourceClick, agentTimeline }: MessageListProps) => {
  return (
    <div className="message-list">
      {agentTimeline}
      {messages.map((msg, i) => (
        <motion.div
          key={i}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className={`message ${msg.role}`}
        >
          <div className="avatar">
            {msg.role === "user" ? "U" : "J"}
          </div>
          <div className="content">
            <div className="answer">{msg.content}</div>
            {msg.agentRun && (
              <div className="agent-meta" style={{ fontSize: 10, color: "#888", marginTop: 4 }}>
                {msg.agentRun.total_steps} steps · {((msg.agentRun.duration_ms || 0) / 1000).toFixed(1)}s · ${(msg.agentRun.total_cost_usd || 0).toFixed(4)}
              </div>
            )}
            {msg.sources && msg.sources.length > 0 && (
              <div className="source-row">
                {msg.sources.map((src, si) => {
                  let host = src;
                  try { host = new URL(src).hostname; } catch {}
                  return (
                    <button
                      key={si}
                      className="source-chip"
                      onClick={() => onSourceClick(src)}
                    >
                      [{si + 1}] {host}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </motion.div>
      ))}
    </div>
  );
};
