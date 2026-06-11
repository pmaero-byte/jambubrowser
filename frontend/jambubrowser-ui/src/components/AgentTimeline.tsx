import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  CheckCircle2, XCircle, Circle, Loader2, Wrench, Search, Brain,
  Globe, Database, Shield, Code2, Bookmark, Target, AlertCircle,
} from "lucide-react";
import type { AgentEvent, PlanStep } from "../utils/types";

interface AgentTimelineProps {
  events: AgentEvent[];
  isActive: boolean;
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
      label = `Plan created (${data.plan?.steps?.length || 0} steps)`;
      color = "#a78bfa";
      break;
    case "step_started": {
      const step: PlanStep = data.step;
      icon = getToolIcon(step.tool);
      label = step.description;
      detail = step.tool ? `tool: ${step.tool}` : "reasoning";
      color = "#4facfe";
      break;
    }
    case "tool_called": {
      icon = getToolIcon(data.tool);
      label = `${data.tool} → success`;
      const dur = data.result?.duration_ms?.toFixed(0) || "?";
      detail = `took ${dur}ms`;
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
    case "answer_ready":
      icon = CheckCircle2;
      label = "Answer ready";
      detail = `${(data.sources || []).length} source(s)`;
      color = "#22c55e";
      break;
    case "run_completed":
      icon = CheckCircle2;
      label = "Run completed";
      detail = `${data.total_steps} steps · ${(data.duration_ms / 1000).toFixed(1)}s · $${data.total_cost_usd?.toFixed(4) || "0.0000"}`;
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
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2 }}
      className="timeline-row"
      style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "6px 8px" }}
    >
      <Icon size={14} color={color} style={{ marginTop: 2, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: "#ddd" }}>{label}</div>
        {detail && (
          <div
            style={{
              fontSize: 11,
              color: "#888",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={detail}
          >
            {detail}
          </div>
        )}
      </div>
    </motion.div>
  );
}

export const AgentTimeline: React.FC<AgentTimelineProps> = ({ events, isActive }) => {
  if (events.length === 0 && !isActive) return null;
  return (
    <div
      className="agent-timeline"
      style={{
        background: "rgba(0, 0, 0, 0.2)",
        border: "1px solid rgba(255, 255, 255, 0.06)",
        borderRadius: 8,
        padding: 4,
        marginBottom: 8,
        maxHeight: 220,
        overflowY: "auto",
      }}
    >
      <AnimatePresence initial={false}>
        {events.map((ev, i) => (
          <EventRow key={`${ev.run_id}-${i}-${ev.type}`} event={ev} />
        ))}
      </AnimatePresence>
      {isActive && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 8, color: "#4facfe", fontSize: 12 }}>
          <Loader2 size={12} className="spin" />
          <span>Agent working…</span>
        </div>
      )}
    </div>
  );
};
