/**
 * AgentWorking — animated "agent is doing things" indicator.
 *
 * Shows a hero brain/loader with the latest tool calls as glowing nodes,
 * connected by an animated trace line. Designed to feel alive without being
 * distracting — a complement to AgentTimeline (which is the dense log).
 *
 * Usage:
 *   <AgentWorking events={agentEvents} isActive={isLoading} />
 */

import { useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Search,
  Globe,
  Database,
  Brain,
  Bookmark,
  Shield,
  Code2,
  Target,
  CheckCircle2,
  Wrench,
  Sparkles,
} from "lucide-react";
import type { AgentEvent } from "../../utils/types";

interface AgentWorkingProps {
  events: AgentEvent[];
  isActive: boolean;
  className?: string;
}

const TOOL_ICONS: Record<string, React.ElementType> = {
  web_search: Search,
  scrape_url: Globe,
  knowledge_query: Database,
  memory_recall: Brain,
  memory_store: Bookmark,
  vault_get: Shield,
  code_exec: Code2,
  goal_set: Target,
  risk_check: Shield,
  final_answer: CheckCircle2,
};

function toolIconFor(name: string | null | undefined): React.ElementType {
  if (!name) return Wrench;
  return TOOL_ICONS[name] || Wrench;
}

// Tool calls extracted from the event stream, most-recent first.
function recentToolCalls(events: AgentEvent[], max = 5) {
  const out: { tool: string; status: "ok" | "failed" | "running"; at: number }[] = [];
  // Walk in chronological order so the most recent ends up at the end of the
  // rendered list (top of the row, where the user's eye goes).
  for (const ev of events) {
    if (ev.type === "step_started") {
      out.push({ tool: ev.data.step?.tool || "reasoning", status: "running", at: ev.timestamp });
    } else if (ev.type === "tool_called") {
      const i = out.findIndex((x) => x.tool === ev.data.tool && x.status === "running");
      if (i >= 0) out[i] = { tool: ev.data.tool, status: "ok", at: ev.timestamp };
      else out.push({ tool: ev.data.tool, status: "ok", at: ev.timestamp });
    } else if (ev.type === "tool_failed") {
      const i = out.findIndex((x) => x.tool === ev.data.tool && x.status === "running");
      if (i >= 0) out[i] = { tool: ev.data.tool, status: "failed", at: ev.timestamp };
      else out.push({ tool: ev.data.tool, status: "failed", at: ev.timestamp });
    }
  }
  return out.slice(-max);
}

export function AgentWorking({ events, isActive, className }: AgentWorkingProps) {
  const tools = useMemo(() => recentToolCalls(events), [events]);
  const latest = events[events.length - 1];
  const phaseLabel = useMemo(() => {
    if (!isActive) {
      if (latest?.type === "run_completed") return "Done";
      if (latest?.type === "run_failed") return "Failed";
      return "Idle";
    }
    if (!latest) return "Warming up…";
    switch (latest.type) {
      case "run_started": return "Reading the question…";
      case "plan_created": return "Mapping a plan…";
      case "step_started": return `Calling ${latest.data.step?.tool || "tool"}…`;
      case "tool_called": return `Got result from ${latest.data.tool}`;
      case "tool_failed": return `Recovering from ${latest.data.tool} failure…`;
      case "step_verified": return "Checking progress…";
      case "replanned": return "Replanning…";
      case "answer_ready": return "Composing the answer…";
      default: return "Working…";
    }
  }, [isActive, latest]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22 }}
      className={
        "rounded-xl border border-border bg-card/60 p-4 overflow-hidden relative " +
        (className || "")
      }
    >
      {/* Soft background pulse so the card itself feels alive. */}
      <motion.div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-0"
        style={{
          background:
            "radial-gradient(ellipse at 30% 0%, rgba(99,102,241,0.18), transparent 60%), radial-gradient(ellipse at 80% 100%, rgba(236,72,153,0.12), transparent 60%)",
        }}
        animate={isActive ? { opacity: [0.6, 1, 0.6] } : { opacity: 0.4 }}
        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
      />

      <div className="relative z-10 flex items-center gap-4">
        {/* Animated brain / sparkles badge. */}
        <div className="relative shrink-0">
          <motion.div
            className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/15 text-primary ring-1 ring-primary/30"
            animate={
              isActive
                ? { rotate: [0, 6, -4, 0], scale: [1, 1.04, 0.98, 1] }
                : { rotate: 0, scale: 1 }
            }
            transition={{ duration: 2.4, repeat: isActive ? Infinity : 0, ease: "easeInOut" }}
          >
            {isActive ? (
              <motion.span
                animate={{ rotate: 360 }}
                transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
                className="block"
              >
                <Sparkles size={22} />
              </motion.span>
            ) : (
              <Brain size={22} />
            )}
          </motion.div>
          {/* Pulse ring */}
          {isActive && (
            <motion.span
              aria-hidden
              className="absolute inset-0 rounded-2xl ring-2 ring-primary/40"
              animate={{ scale: [1, 1.4], opacity: [0.7, 0] }}
              transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
            />
          )}
        </div>

        {/* Phase label + status dot */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold tracking-tight">
              {isActive ? "Agent" : "Last run"}
            </span>
            {isActive && (
              <span className="relative flex h-2 w-2">
                <motion.span
                  className="absolute inline-flex h-full w-full rounded-full bg-emerald-400"
                  animate={{ scale: [1, 2.2], opacity: [0.6, 0] }}
                  transition={{ duration: 1.4, repeat: Infinity, ease: "easeOut" }}
                />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>
            )}
          </div>
          <div className="truncate text-xs text-muted-foreground" title={phaseLabel}>
            {phaseLabel}
          </div>
        </div>
      </div>

      {/* Tool nodes — recent tool calls as glowing chips connected by a trace line. */}
      {tools.length > 0 && (
        <div className="relative z-10 mt-4">
          <div className="flex items-center gap-2 overflow-x-auto pb-1">
            <AnimatePresence initial={false}>
              {tools.map((t, i) => {
                const Icon = toolIconFor(t.tool);
                const isLatest = i === tools.length - 1 && isActive;
                return (
                  <motion.div
                    key={`${t.tool}-${t.at}-${i}`}
                    layout
                    initial={{ opacity: 0, scale: 0.6, y: 6 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.6 }}
                    transition={{ type: "spring", stiffness: 320, damping: 24 }}
                    className="relative shrink-0"
                  >
                    <motion.div
                      className={
                        "flex h-9 w-9 items-center justify-center rounded-full border " +
                        (t.status === "ok"
                          ? "bg-emerald-400/10 border-emerald-400/30 text-emerald-300"
                          : t.status === "failed"
                            ? "bg-red-400/10 border-red-400/30 text-red-300"
                            : "bg-blue-400/10 border-blue-400/40 text-blue-300")
                      }
                      animate={
                        isLatest
                          ? { boxShadow: [
                              "0 0 0 0 rgba(96,165,250,0.4)",
                              "0 0 0 10px rgba(96,165,250,0)",
                            ] }
                          : { boxShadow: "0 0 0 0 rgba(0,0,0,0)" }
                      }
                      transition={{ duration: 1.4, repeat: isLatest ? Infinity : 0 }}
                    >
                      <Icon size={15} />
                    </motion.div>
                    {/* Tool name on hover */}
                    <div className="pointer-events-none absolute -bottom-5 left-1/2 -translate-x-1/2 whitespace-nowrap text-[10px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
                      {t.tool}
                    </div>
                  </motion.div>
                );
              })}
            </AnimatePresence>
          </div>

          {/* Trace line — a thin horizontal line that flows left-to-right while active. */}
          <div className="relative mt-2 h-px w-full overflow-hidden rounded bg-border/60">
            <motion.div
              aria-hidden
              className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-primary to-transparent"
              animate={{ x: ["-100%", "300%"] }}
              transition={{ duration: 1.8, repeat: isActive ? Infinity : 0, ease: "linear" }}
            />
          </div>
        </div>
      )}
    </motion.div>
  );
}
