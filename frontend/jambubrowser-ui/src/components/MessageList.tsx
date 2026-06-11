import React, { useState } from "react";
import { motion } from "framer-motion";
import { ExternalLink, ChevronDown, ChevronUp } from "lucide-react";

/**
 * Premium Message List
 * --------------------
 * Renders the flow of conversation.
 * Handles 'Deep Trust' source highlighting.
 * Optionally renders an AgentTimeline above the latest message
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
  /** Show a typing cursor on the most recent message while streaming */
  isStreaming?: boolean;
}

function shortHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 32);
  }
}

export const MessageList = ({ messages, onSourceClick, agentTimeline, isStreaming }: MessageListProps) => {
  return (
    <div className="message-list">
      {agentTimeline}
      {messages.map((msg, i) => {
        const isLast = i === messages.length - 1;
        const showCursor = isStreaming && isLast && msg.role === "assistant" && msg.content === "";
        return (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18 }}
            className={`message ${msg.role}`}
          >
            <div className="avatar">
              {msg.role === "user" ? "U" : "J"}
            </div>
            <div className="content">
              <div className="answer">
                {msg.content}
                {showCursor && <TypingCursor />}
                {!msg.content && msg.role === "assistant" && !isStreaming && (
                  <span style={{ color: "#888", fontStyle: "italic" }}>…</span>
                )}
              </div>
              {msg.agentRun && (
                <div className="agent-meta">
                  <span><b>{msg.agentRun.total_steps}</b> steps</span>
                  <span><b>{((msg.agentRun.duration_ms || 0) / 1000).toFixed(1)}s</b></span>
                  <span><b>${(msg.agentRun.total_cost_usd || 0).toFixed(4)}</b></span>
                </div>
              )}
              {msg.sources && msg.sources.length > 0 && (
                <SourceChips sources={msg.sources} onSourceClick={onSourceClick} />
              )}
            </div>
          </motion.div>
        );
      })}
    </div>
  );
};

const TypingCursor: React.FC = () => (
  <motion.span
    className="typing-cursor"
    animate={{ opacity: [1, 0, 1] }}
    transition={{ duration: 1, repeat: Infinity, ease: "easeInOut" }}
  >
    ▊
  </motion.span>
);

const SourceChips: React.FC<{ sources: string[]; onSourceClick: (url: string) => void }> = ({ sources, onSourceClick }) => {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? sources : sources.slice(0, 3);
  const hidden = sources.length - visible.length;
  return (
    <div className="source-row">
      {visible.map((src, si) => (
        <button
          key={si}
          className="source-chip"
          onClick={() => onSourceClick(src)}
          title={src}
        >
          <ExternalLink size={9} /> {shortHost(src)}
        </button>
      ))}
      {hidden > 0 && (
        <button
          className="source-chip source-chip-more"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? (
            <><ChevronUp size={9} /> less</>
          ) : (
            <><ChevronDown size={9} /> +{hidden} more</>
          )}
        </button>
      )}
    </div>
  );
};
