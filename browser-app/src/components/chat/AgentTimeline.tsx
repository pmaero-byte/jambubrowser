import { useRef, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  CheckCircle2,
  XCircle,
  Circle,
  Loader2,
  Wrench,
  Search,
  Brain,
  Globe,
  Database,
  Shield,
  Code2,
  Bookmark,
  Target,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Clock,
  Hash,
  DollarSign,
  X,
} from "lucide-react";
import type { AgentEvent, PlanStep } from "../../utils/types";
import { useState } from "react";

interface AgentTimelineProps {
  events: AgentEvent[];
  isActive: boolean;
  onDismiss?: () => void;
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

function getToolIcon(name: string | null) {
  if (!name) return Circle;
  return TOOL_ICONS[name] || Wrench;
}

function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 32);
  }
}

function EventRow({ event }: { event: AgentEvent }) {
  const { type, data } = event;
  let Icon: React.ElementType = Circle;
  let label = "";
  let detail: string | null = null;
  let colorClass = "text-muted-foreground";

  switch (type) {
    case "run_started":
      Icon = Loader2;
      label = "Starting research";
      detail = data.query;
      colorClass = "text-blue-400";
      break;
    case "plan_created": {
      Icon = Brain;
      const n = data.plan?.steps?.length || 0;
      label = `Plan created (${n} step${n === 1 ? "" : "s"})`;
      colorClass = "text-violet-400";
      break;
    }
    case "step_started": {
      const step: PlanStep = data.step;
      Icon = getToolIcon(step.tool);
      label = step.description;
      detail = step.tool ? `→ ${step.tool}` : "reasoning";
      colorClass = "text-blue-400";
      break;
    }
    case "tool_called": {
      Icon = getToolIcon(data.tool);
      {
        const dur = data.result?.duration_ms?.toFixed(0) || "?";
        const out = data.result?.data;
        const resultCount =
          out?.count ?? out?.results?.length ?? (typeof out === "object" ? Object.keys(out || {}).length : 0);
        label = `${data.tool} succeeded`;
        detail = `${dur}ms${resultCount ? ` · ${resultCount} result${resultCount === 1 ? "" : "s"}` : ""}`;
      }
      colorClass = "text-emerald-400";
      break;
    }
    case "tool_failed":
      Icon = XCircle;
      label = `${data.tool} failed`;
      detail = data.error;
      colorClass = "text-red-400";
      break;
    case "step_verified": {
      const v = data.verdict;
      Icon = v?.advanced ? CheckCircle2 : AlertCircle;
      label = v?.advanced ? "Step advanced goal" : "Step did not advance";
      detail = v?.feedback;
      colorClass = v?.advanced ? "text-emerald-400" : "text-amber-400";
      break;
    }
    case "replanned":
      Icon = Brain;
      label = "Replanning";
      detail = data.reason;
      colorClass = "text-amber-400";
      break;
    case "answer_ready": {
      const sources: string[] = data.sources || [];
      Icon = CheckCircle2;
      label = "Answer ready";
      detail =
        sources.length > 0
          ? `${sources.length} source${sources.length === 1 ? "" : "s"}: ${sources
              .slice(0, 3)
              .map(shortUrl)
              .join(", ")}${sources.length > 3 ? "…" : ""}`
          : "no external sources";
      colorClass = "text-emerald-400";
      break;
    }
    case "run_completed":
      Icon = CheckCircle2;
      label = "Run completed";
      detail = `${data.total_steps} step${data.total_steps === 1 ? "" : "s"} · ${(data.duration_ms / 1000).toFixed(
        1,
      )}s · $${(data.total_cost_usd || 0).toFixed(4)}`;
      colorClass = "text-emerald-400";
      break;
    case "run_failed":
      Icon = XCircle;
      label = "Run failed";
      detail = data.error;
      colorClass = "text-red-400";
      break;
    case "log":
      Icon = AlertCircle;
      label = data.message;
      break;
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: -8, scale: 0.98 }}
      animate={{ opacity: 1, x: 0, scale: 1 }}
      transition={{ duration: 0.18 }}
      className="flex items-start gap-3 py-1.5"
    >
      <div className={`mt-0.5 ${colorClass}`}>
        {type === "run_started" ? (
          <motion.span animate={{ rotate: 360 }} transition={{ duration: 1.5, repeat: Infinity, ease: "linear" }}>
            <Icon size={13} />
          </motion.span>
        ) : (
          <Icon size={13} />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{label}</div>
        {detail && (
          <div className="truncate text-xs text-muted-foreground" title={detail}>
            {detail}
          </div>
        )}
      </div>
      <span className="shrink-0 text-[10px] text-muted-foreground">
        {new Date(event.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      </span>
    </motion.div>
  );
}

export function AgentTimeline({ events, isActive, onDismiss }: AgentTimelineProps) {
  const [collapsed, setCollapsed] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const summary = useMemo(() => {
    const ready = events.find((e) => e.type === "answer_ready");
    const completed = events.find((e) => e.type === "run_completed");
    if (!completed) return null;
    return {
      steps: completed.data.total_steps as number,
      duration_ms: completed.data.duration_ms as number,
      cost: completed.data.total_cost_usd as number,
      sources: (ready?.data.sources as string[]) || [],
    };
  }, [events]);

  useEffect(() => {
    if (!collapsed && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [events.length, collapsed]);

  if (events.length === 0 && !isActive) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl border border-border bg-card p-3"
    >
      <div
        className="flex cursor-pointer items-center justify-between"
        onClick={() => setCollapsed((c) => !c)}
      >
        <div className="flex items-center gap-2">
          {isActive ? (
            <Loader2 size={14} className="animate-spin text-blue-400" />
          ) : (
            <Brain size={14} className="text-accent" />
          )}
          <span className="text-sm font-semibold">{isActive ? "Agent working" : "Agent timeline"}</span>
          {summary && !isActive && (
            <span className="ml-3 flex items-center gap-2 text-xs text-muted-foreground">
              <Hash size={10} /> {summary.steps}
              <Clock size={10} /> {(summary.duration_ms / 1000).toFixed(1)}s
              <DollarSign size={10} /> {(summary.cost || 0).toFixed(4)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {onDismiss && !isActive && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDismiss();
              }}
              className="rounded p-1 hover:bg-muted"
              title="Dismiss"
            >
              <X size={12} />
            </button>
          )}
          <button className="rounded p-1 hover:bg-muted">
            {collapsed ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
          </button>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div ref={ref} className="mt-2 max-h-72 overflow-y-auto pr-1">
              <AnimatePresence initial={false}>
                {events.map((ev, i) => (
                  <EventRow key={`${ev.run_id}-${i}-${ev.type}`} event={ev} />
                ))}
              </AnimatePresence>
              {isActive && (
                <div className="flex items-center gap-2 py-1.5 text-xs text-muted-foreground">
                  <motion.div
                    className="h-2 w-2 rounded-full bg-accent"
                    animate={{ scale: [1, 1.4, 1], opacity: [0.5, 1, 0.5] }}
                    transition={{ duration: 1.2, repeat: Infinity }}
                  />
                  <span>Agent working…</span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
