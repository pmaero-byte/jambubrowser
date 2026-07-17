/**
 * TelemetryPanel — live readout of model + tokens + current action.
 *
 * Mirrors the contract in .omo/plans/agent-visualization.md §6.
 * Subscribes to the same useAgentWebSocket hook that powers AgentRoom so
 * both components show a synchronized view.
 *
 * Props match the spec:
 *   - model          : current LLM model id (e.g. "gemma4:12b-it-qat")
 *   - tokensPerSec   : rolling 1s tokens/sec
 *   - currentAction  : human-readable action string
 *   - reasoningTrace : accumulated LLM reasoning text
 *   - fileBreadcrumb : optional file_path currently being acted on
 *   - contextSize    : optional context window size in tokens
 */

import { useMemo } from "react";
import { motion } from "motion/react";
import {
  Cpu,
  Gauge,
  FileText,
  BookOpen,
  Hash,
  Activity,
  Brain,
} from "lucide-react";

export interface TelemetryPanelProps {
  model: string;
  tokensPerSec?: number;
  currentAction: string;
  reasoningTrace: string;
  fileBreadcrumb?: string;
  contextSize?: number;
  tokensGenerated?: number;
  taskActive?: boolean;
}

function formatTps(tps?: number): string {
  if (tps === undefined || tps === null) return "—";
  if (tps < 1) return `${tps.toFixed(2)} tok/s`;
  return `${tps.toFixed(1)} tok/s`;
}

function formatCount(n?: number): string {
  if (n === undefined || n === null) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function TelemetryPanel({
  model,
  tokensPerSec,
  currentAction,
  reasoningTrace,
  fileBreadcrumb,
  contextSize,
  tokensGenerated,
  taskActive,
}: TelemetryPanelProps) {
  const lastReasoning = useMemo(() => {
    const trimmed = reasoningTrace.trim();
    if (!trimmed) return "(no reasoning yet)";
    // Show last 600 chars, with leading "…" when truncated.
    return trimmed.length > 600 ? `…${trimmed.slice(-600)}` : trimmed;
  }, [reasoningTrace]);

  return (
    <div className="flex h-full flex-col gap-3 overflow-hidden p-3">
      {/* Model + status row */}
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card/60 p-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-accent/15 text-accent ring-1 ring-accent/30">
          <Brain size={14} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-xs font-medium" title={model}>
            {model}
          </div>
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            <span
              className={
                "inline-block h-1.5 w-1.5 rounded-full " +
                (taskActive ? "bg-emerald-400 animate-pulse" : "bg-muted-foreground/40")
              }
            />
            {taskActive ? "running" : "idle"}
          </div>
        </div>
      </div>

      {/* Metrics grid: 2x2 */}
      <div className="grid grid-cols-2 gap-2">
        <MetricTile icon={<Gauge size={11} />} label="Tokens/s" value={formatTps(tokensPerSec)} />
        <MetricTile icon={<Hash size={11} />} label="Generated" value={formatCount(tokensGenerated)} />
        <MetricTile icon={<Activity size={11} />} label="Context" value={formatCount(contextSize)} />
        <MetricTile icon={<Cpu size={11} />} label="Model" value={shortModel(model)} />
      </div>

      {/* Current action */}
      <div className="rounded-lg border border-border bg-card/60 p-2">
        <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
          <Activity size={10} /> Current action
        </div>
        <motion.div
          key={currentAction}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
          className="truncate text-xs font-medium"
          title={currentAction}
        >
          {currentAction || "(none)"}
        </motion.div>
        {fileBreadcrumb && (
          <div className="mt-1 flex items-center gap-1 truncate text-[10px] text-muted-foreground" title={fileBreadcrumb}>
            <FileText size={10} className="shrink-0" />
            <span className="truncate font-mono">{fileBreadcrumb}</span>
          </div>
        )}
      </div>

      {/* Reasoning trace */}
      <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card/60">
        <div className="flex items-center gap-1 border-b border-border/50 px-2 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
          <BookOpen size={10} /> Reasoning trace
        </div>
        <div className="flex-1 overflow-y-auto p-2 font-mono text-[11px] leading-relaxed text-foreground/80">
          {lastReasoning}
        </div>
      </div>
    </div>
  );
}

function MetricTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border border-border/60 bg-surface/50 p-1.5">
      <div className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-muted-foreground">
        {icon} {label}
      </div>
      <div className="mt-0.5 truncate font-mono text-xs font-medium tabular-nums" title={value}>
        {value}
      </div>
    </div>
  );
}

function shortModel(model: string): string {
  if (!model) return "—";
  // "gemma4:12b-it-qat" → "12b"
  const m = model.match(/(\d+[bm])/i);
  if (m) return m[1].toLowerCase();
  return model.length > 10 ? `${model.slice(0, 10)}…` : model;
}
