/**
 * InterruptionInput — floating redirect bar shown during an active task.
 *
 * Per the spec in .omo/plans/agent-visualization.md §5 + §6:
 *   - Visible only when an agent.task_start is active
 *   - User types instruction → Enter or "Redirect" submits
 *   - Backend POST /interrupt/{task_id} with { new_instruction, client_id }
 *   - Old streaming output is preserved with [INTERRUPTED] tag (handled
 *     by the backend broadcast_task_end with status="interrupted")
 *
 * Props:
 *   - visible    : whether to render the bar
 *   - taskId     : current task_id (so we know which task to interrupt)
 *   - onSubmit   : callback that POSTs /interrupt and starts the new task
 *   - onCancel   : callback to dismiss the bar without interrupting
 */

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Send, X, AlertTriangle } from "lucide-react";

export interface InterruptionInputProps {
  visible: boolean;
  taskId?: string;
  onSubmit: (instruction: string) => void;
  onCancel: () => void;
}

export function InterruptionInput({
  visible,
  taskId,
  onSubmit,
  onCancel,
}: InterruptionInputProps) {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (visible) {
      // Auto-focus when bar appears.
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      setText("");
    }
  }, [visible]);

  const handleSubmit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setText("");
  };

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 24 }}
          transition={{ type: "spring", stiffness: 320, damping: 28 }}
          className="fixed inset-x-0 bottom-6 z-50 mx-auto w-full max-w-2xl px-4"
        >
          <div className="overflow-hidden rounded-2xl border border-amber-500/40 bg-amber-500/10 shadow-2xl backdrop-blur-md">
            <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-300/90">
              <AlertTriangle size={12} className="shrink-0" />
              <span className="font-medium">
                Interrupting task
                {taskId && <code className="ml-1 rounded bg-amber-500/20 px-1 font-mono text-[10px]">{taskId}</code>}
              </span>
              <span className="text-amber-300/60">·</span>
              <span className="text-amber-300/60">type a redirect, then press Enter</span>
              <button
                onClick={onCancel}
                title="Cancel"
                className="ml-auto rounded p-0.5 text-amber-300/60 transition-colors hover:bg-amber-500/20 hover:text-amber-300"
              >
                <X size={12} />
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSubmit();
              }}
              className="flex items-center gap-2 px-3 py-2"
            >
              <input
                ref={inputRef}
                type="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Redirect the agent — e.g. 'skip the comparison, just summarize'"
                className="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-foreground/40 focus:outline-none"
              />
              <button
                type="submit"
                disabled={!text.trim()}
                className="inline-flex items-center gap-1.5 rounded-md bg-amber-500/30 px-3 py-1 text-xs font-medium text-amber-100 transition-all hover:bg-amber-500/50 disabled:opacity-40"
              >
                <Send size={11} />
                Redirect
              </button>
            </form>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
