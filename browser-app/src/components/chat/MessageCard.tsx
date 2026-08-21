import { useState } from "react";
import { motion } from "motion/react";
import { ExternalLink, ChevronDown, ChevronUp, User, Bot, Wrench } from "lucide-react";
import type { ChatMessage } from "../../store/appStore";

interface MessageCardProps {
  message: ChatMessage;
  isStreaming?: boolean;
  isLast?: boolean;
  onSourceClick?: (url: string) => void;
}

function shortHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 32);
  }
}

/**
 * RevealText — splits content into characters and fades each one in. Only
 * used while streaming the latest assistant message. We use a tiny per-char
 * delay (capped) so the effect is felt at any text length.
 */
function RevealText({ text }: { text: string }) {
  // Cap visible length to avoid remounting hundreds of motion spans on a
  // long final answer. For typical chat (<2k chars) this is a no-op.
  const chars = Array.from(text);
  const cap = 600;
  const truncated = chars.length > cap;
  const visible = chars.slice(0, cap);
  return (
    <>
      {visible.map((c, i) => (
        <motion.span
          key={i}
          initial={{ opacity: 0, y: 1 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18, delay: Math.min(i * 0.004, 1.4) }}
        >
          {c}
        </motion.span>
      ))}
      {truncated && <span>{chars.slice(cap).join("")}</span>}
    </>
  );
}

export function MessageCard({
  message,
  isStreaming,
  isLast,
  onSourceClick,
}: MessageCardProps) {
  const isAssistant = message.role === "assistant";
  const showCursor = isStreaming && isLast && isAssistant && message.content === "";
  // Per-character reveal only on the actively-streaming final assistant msg.
  const reveal = isStreaming && isLast && isAssistant && message.content.length > 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={`flex gap-3 p-4 ${isAssistant ? "bg-card/40" : ""}`}
    >
      <div
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs ${
          isAssistant
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {isAssistant ? <Bot size={14} /> : <User size={14} />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="prose prose-sm max-w-none text-sm leading-relaxed whitespace-pre-wrap">
          {reveal ? <RevealText text={message.content} /> : message.content}
          {showCursor && <TypingCursor />}
          {!message.content && isAssistant && !isStreaming && (
            <span className="text-muted-foreground italic">…</span>
          )}
        </div>
        {message.agentRun && (
          <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
            <span>
              <b>{message.agentRun.total_steps}</b> steps
            </span>
            <span>
              <b>{((message.agentRun.duration_ms || 0) / 1000).toFixed(1)}</b>s
            </span>
            <span>
              <b>${(message.agentRun.total_cost_usd || 0).toFixed(4)}</b>
            </span>
          </div>
        )}
        {message.sources && message.sources.length > 0 && (
          <SourceChips sources={message.sources} onSourceClick={onSourceClick} />
        )}
      </div>
    </motion.div>
  );
}

function TypingCursor() {
  return (
    <motion.span
      className="ml-0.5 inline-block h-4 w-2 align-text-bottom bg-accent"
      animate={{ opacity: [1, 0, 1] }}
      transition={{ duration: 1, repeat: Infinity, ease: "easeInOut" }}
    />
  );
}

function SourceChips({
  sources,
  onSourceClick,
}: {
  sources: string[];
  onSourceClick?: (url: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? sources : sources.slice(0, 3);
  const hidden = sources.length - visible.length;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <Wrench size={12} className="text-muted-foreground" />
      {visible.map((src, si) => (
        <button
          key={si}
          className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground hover:border-accent transition-colors"
          onClick={() => onSourceClick?.(src)}
          title={src}
        >
          <ExternalLink size={9} />
          {shortHost(src)}
        </button>
      ))}
      {(hidden > 0 || expanded) && (
        <button
          className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? (
            <>
              <ChevronUp size={9} /> less
            </>
          ) : (
            <>
              <ChevronDown size={9} /> +{hidden} more
            </>
          )}
        </button>
      )}
    </div>
  );
}