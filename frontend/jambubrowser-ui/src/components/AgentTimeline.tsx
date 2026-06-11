import React, { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  CheckCircle2, XCircle, Circle, Loader2, Wrench, Search, Brain,
  Globe, Database, Shield, Code2, Bookmark, Target, AlertCircle,
  ChevronDown, ChevronUp, Clock, Hash, DollarSign, X,
} from "lucide-react";
import type { AgentEvent, PlanStep } from "../utils/types";

interface AgentTimelineProps {
  events: AgentEvent[];
  isActive: boolean;
  /** Called when user clicks the X to dismiss a completed run */
  onDismiss?: () => void;
}

const TOOL_ICONS: Record<string, any> = {
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
  let icon: any = Circle;
  let label = "";
  let color = "#888";
  let detail: string | null = null;

  switch (type) {
    case "run_started":
      icon = Loader2;
      label = "Starting research";
      detail = data.query;
      color = "#4facfe";
      break;
    case "plan_created":
      icon = Brain;
      const n = data.plan?.steps?.length || 0;
      label = `Plan created (${n} step${n === 1 ? "" : "s"})`;
      color = "#a78bfa";
      break;
    case "step_started": {
      const step: PlanStep = data.step;
      icon = getToolIcon(step.tool);
      label = step.description;
      detail = step.tool ? `→ ${step.tool}` : "reasoning";
      color = "#4facfe";
      break;
    }
    case "tool_called": {
      icon = getToolIcon(data.tool);
      const dur = data.result?.duration_ms?.toFixed(0) || "?";
      const out = data.result?.data;
      const resultCount =
        out?.count ?? out?.results?.length ?? (typeof out === "object" ? Object.keys(out || {}).length : 0);
      label = `${data.tool} succeeded`;
      detail = `${dur}ms${resultCount ? ` · ${resultCount} result${resultCount === 1 ? "" : "s"}` : ""}`;
      color = "#22c55e";
      break;
    }
    case "tool_failed":
      icon = XCircle;
      label = `${data.tool} failed`;
      detail = data.error;
      color = "#ef4444";
      break;
    case "step_verified": {
      const v = data.verdict;
      icon = v?.advanced ? CheckCircle2 : AlertCircle;
      label = v?.advanced ? "Step advanced goal" : "Step did not advance";
      detail = v?.feedback;
      color = v?.advanced ? "#22c55e" : "#f59e0b";
      break;
    }
    case "replanned":
      icon = Brain;
      label = "Replanning";
      detail = data.reason;
      color = "#f59e0b";
      break;
    case "answer_ready": {
      const sources: string[] = data.sources || [];
      icon = CheckCircle2;
      label = "Answer ready";
      detail = sources.length > 0
        ? `${sources.length} source${sources.length === 1 ? "" : "s"}: ${sources.slice(0, 3).map(shortUrl).join(", ")}${sources.length > 3 ? "…" : ""}`
        : "no external sources";
      color = "#22c55e";
      break;
    }
    case "run_completed":
      icon = CheckCircle2;
      label = "Run completed";
      detail = `${data.total_steps} step${data.total_steps === 1 ? "" : "s"} · ${(data.duration_ms / 1000).toFixed(1)}s · $${(data.total_cost_usd || 0).toFixed(4)}`;
      color = "#22c55e";
      break;
    case "run_failed":
      icon = XCircle;
      label = "Run failed";
      detail = data.error;
      color = "#ef4444";
      break;
    case "log":
      icon = AlertCircle;
      label = data.message;
      color = "#888";
      break;
  }
  const Icon = icon;
  return (
    <motion.div
      initial={{ opacity: 0, x: -8, scale: 0.98 }}
      animate={{ opacity: 1, x: 0, scale: 1 }}
      transition={{ duration: 0.18 }}
      className="timeline-row"
    >
      <div className="timeline-icon" style={{ color }}>
        {type === "run_started" ? (
          <motion.span animate={{ rotate: 360 }} transition={{ duration: 1.5, repeat: Infinity, ease: "linear" }}>
            <Icon size={13} />
          </motion.span>
        ) : (
          <Icon size={13} />
        )}
      </div>
      <div className="timeline-text">
        <div className="timeline-label">{label}</div>
        {detail && <div className="timeline-detail" title={detail}>{detail}</div>}
      </div>
      <span className="timeline-time">
        {new Date(event.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
      </span>
    </motion.div>
  );
}

export const AgentTimeline: React.FC<AgentTimelineProps> = ({ events, isActive, onDismiss }) => {
  const [collapsed, setCollapsed] = useState(false);
  const [autoScroll] = useState(true);

  // Find the most recent answer_ready for the summary
  const summary = useMemo(() => {
    const ready = events.find((e) => e.type === "answer_ready");
    const completed = events.find((e) => e.type === "run_completed");
    if (!completed) return null;
    return {
      steps: completed.data.total_steps as number,
      duration_ms: completed.data.duration_ms as number,
      cost: completed.data.total_cost_usd as number,
      answerLength: ready ? (ready.data.answer as string).length : 0,
      sources: (ready?.data.sources as string[]) || [],
    };
  }, [events]);

  // Auto-scroll to bottom
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (autoScroll && !collapsed && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [events.length, autoScroll, collapsed]);

  if (events.length === 0 && !isActive) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="agent-timeline"
    >
      <div className="timeline-header" onClick={() => setCollapsed((c) => !c)}>
        <div className="timeline-header-left">
          {isActive ? (
            <Loader2 size={12} className="spin" color="#4facfe" />
          ) : (
            <Brain size={12} color="var(--accent)" />
          )}
          <span className="timeline-title">
            {isActive ? "Agent working" : "Agent timeline"}
          </span>
          {summary && !isActive && (
            <span className="timeline-summary">
              <Hash size={10} /> {summary.steps}
              <Clock size={10} style={{ marginLeft: 8 }} /> {(summary.duration_ms / 1000).toFixed(1)}s
              <DollarSign size={10} style={{ marginLeft: 8 }} /> {(summary.cost || 0).toFixed(4)}
            </span>
          )}
        </div>
        <div className="timeline-header-right">
          {onDismiss && !isActive && (
            <button
              onClick={(e) => { e.stopPropagation(); onDismiss(); }}
              className="timeline-btn"
              title="Dismiss"
            >
              <X size={12} />
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); setCollapsed((c) => !c); }}
            className="timeline-btn"
            title={collapsed ? "Expand" : "Collapse"}
          >
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
            className="timeline-body"
            ref={ref}
          >
            <AnimatePresence initial={false}>
              {events.map((ev, i) => (
                <EventRow key={`${ev.run_id}-${i}-${ev.type}`} event={ev} />
              ))}
            </AnimatePresence>
            {isActive && (
              <div className="timeline-active-indicator">
                <motion.div
                  className="active-dot"
                  animate={{ scale: [1, 1.4, 1], opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1.2, repeat: Infinity }}
                />
                <span>Agent working…</span>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};
