import { useRef, useEffect, useState } from "react";
import { Button } from "../ui/button";
import { motion } from "motion/react";
import { ArrowUp, Paperclip, Mic, StopCircle } from "lucide-react";
import { MessageCard } from "./MessageCard";
import { AgentTimeline } from "./AgentTimeline";
import { AgentWorking } from "./AgentWorking";
import { useAppStore } from "../../store/appStore";
import type { AgentEvent } from "../../utils/types";

interface ChatPaneProps {
  agentEvents: AgentEvent[];
  onSend: (text: string) => void;
  onStop?: () => void;
}

export function ChatPane({ agentEvents, onSend, onStop }: ChatPaneProps) {
  const { messages, input, setInput, isLoading, setActiveTab, addBrowserTab, updateBrowserTab } = useAppStore();
  const bottomRef = useRef<HTMLDivElement>(null);
  // Bump this counter on submit so the send button can "punch" via key change.
  const [sendTick, setSendTick] = useState(0);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, agentEvents]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    setSendTick((t) => t + 1);
    onSend(input.trim());
    setInput("");
  };

  const handleSourceClick = (url: string) => {
    addBrowserTab();
    const newId = useAppStore.getState().activeBrowserTabId;
    updateBrowserTab(newId, { url, title: url });
    setActiveTab("browser");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-2">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-muted-foreground">
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
              </svg>
            </div>
            <p className="text-sm font-medium">What would you like to research?</p>
            <p className="mt-1 max-w-sm text-center text-xs">
              Ask a question, run an agent, or open the browser to gather sources.
            </p>
          </div>
        ) : (
          <>
            {messages.map((msg, i) => (
              <MessageCard
                key={msg.id}
                message={msg}
                isLast={i === messages.length - 1}
                isStreaming={isLoading}
                onSourceClick={handleSourceClick}
              />
            ))}
            {(isLoading || agentEvents.length > 0) && (
              <div className="my-3 space-y-3">
                <AgentWorking events={agentEvents} isActive={isLoading} />
                <AgentTimeline
                  events={agentEvents}
                  isActive={isLoading}
                  onDismiss={!isLoading ? () => {} : undefined}
                />
              </div>
            )}
            <div ref={bottomRef} />
          </>
        )}
      </div>

      <div className="border-t border-border bg-card/50 p-3">
        <form onSubmit={handleSubmit} className="flex items-end gap-2 rounded-xl border border-border bg-background p-2">
          <Button type="button" variant="ghost" size="icon" className="h-8 w-8 shrink-0">
            <Paperclip size={16} />
          </Button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            placeholder="Send a command or research query..."
            rows={1}
            className="max-h-32 min-h-[36px] flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          <Button type="button" variant="ghost" size="icon" className="h-8 w-8 shrink-0">
            <Mic size={16} />
          </Button>
          {isLoading ? (
            <Button type="button" variant="secondary" size="icon" className="h-8 w-8 shrink-0" onClick={onStop} title="Stop generation">
              <StopCircle size={16} />
            </Button>
          ) : (
            <motion.div
              key={sendTick} // remount on each submit -> the punch animation restarts
              initial={{ scale: 0.6, rotate: -90 }}
              animate={{ scale: [0.6, 1.15, 1], rotate: [-90, 10, 0] }}
              transition={{ duration: 0.32, ease: "easeOut" }}
              className="shrink-0"
            >
              <Button type="submit" size="icon" className="h-8 w-8" disabled={!input.trim()} title="Submit">
                <ArrowUp size={16} />
              </Button>
            </motion.div>
          )}
        </form>
      </div>
    </div>
  );
}
