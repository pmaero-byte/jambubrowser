import { useState, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Search, Zap, Shield, Activity, ChevronRight,
  Download, Loader2, AlertTriangle, CheckCircle,
  Info, AlertOctagon, XCircle,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Finding {
  id: string;
  employee: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  category: string;
  title: string;
  description: string;
  fix_suggestion: string;
  evidence_snippet: string;
  wcag_criterion?: string;
  score_impact?: string;
}

interface EmployeeResult {
  employee: string;
  emoji: string;
  findings_count: number;
  elapsed_ms: number;
  findings: Finding[];
}

interface AuditSummary {
  total_findings: number;
  by_severity: Record<string, number>;
  url: string;
  mode: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const EMPLOYEE_EMOJI: Record<string, string> = {
  "Security Auditor": "🔒",
  "Performance Inspector": "⚡",
  "UX/UI Reviewer": "🎨",
  "SEO Analyzer": "🔍",
  "Accessibility Auditor": "♿",
  "Code Quality Scout": "🧹",
};

const SEVERITY_CONFIG = {
  critical: { icon: XCircle, color: "text-red-500", bg: "bg-red-500/10", border: "border-red-500/30", label: "Critical" },
  high: { icon: AlertOctagon, color: "text-orange-500", bg: "bg-orange-500/10", border: "border-orange-500/30", label: "High" },
  medium: { icon: AlertTriangle, color: "text-yellow-500", bg: "bg-yellow-500/10", border: "border-yellow-500/30", label: "Medium" },
  low: { icon: Info, color: "text-blue-500", bg: "bg-blue-500/10", border: "border-blue-500/30", label: "Low" },
  info: { icon: CheckCircle, color: "text-green-500", bg: "bg-green-500/10", border: "border-green-500/30", label: "Info" },
};

type GroupBy = "employee" | "severity";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AuditPanel() {
  const [url, setUrl] = useState("");
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<string>("idle");
  const [employeeResults, setEmployeeResults] = useState<EmployeeResult[]>([]);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [groupBy, setGroupBy] = useState<GroupBy>("employee");
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);

  const reset = () => {
    setEmployeeResults([]);
    setSummary(null);
    setPhase("idle");
    setExpandedCards(new Set());
  };

  const toggleCard = (id: string) => {
    setExpandedCards((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // -----------------------------------------------------------------------
  // Run Audit (SSE streaming)
  // -----------------------------------------------------------------------

  const runAudit = useCallback(
    async (mode: "full" | "quick") => {
      if (!url.trim() || running) return;
      reset();
      setRunning(true);
      setPhase("collecting");

      const ac = new AbortController();
      abortRef.current = ac;

      try {
        const res = await localFetch(
          mode === "full" ? "/audit/run" : "/audit/quick",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: url.trim(), mode }),
            signal: ac.signal,
          }
        );

        if (!res.ok || !res.body) {
          setPhase("error");
          setRunning(false);
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          let currentEvent = "";
          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ") && currentEvent) {
              try {
                const data = JSON.parse(line.slice(6));
                handleSSE(currentEvent, data);
              } catch {
                // skip malformed
              }
              currentEvent = "";
            }
          }
        }
      } catch (err: any) {
        if (err?.name !== "AbortError") {
          setPhase("error");
        }
      } finally {
        setRunning(false);
      }
    },
    [url, running]
  );

  const handleSSE = (event: string, data: any) => {
    switch (event) {
      case "status":
        setPhase(data.phase);
        break;
      case "employee_done":
        setEmployeeResults((prev) => [
          ...prev,
          {
            employee: data.employee,
            emoji: data.emoji,
            findings_count: data.findings_count,
            elapsed_ms: data.elapsed_ms,
            findings: data.findings || [],
          },
        ]);
        break;
      case "employee_error":
        setEmployeeResults((prev) => [
          ...prev,
          {
            employee: data.employee,
            emoji: data.emoji,
            findings_count: 0,
            elapsed_ms: data.elapsed_ms,
            findings: [],
          },
        ]);
        break;
      case "done":
        setSummary(data);
        setPhase("done");
        break;
      case "error":
        setPhase("error");
        break;
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    setRunning(false);
    setPhase("idle");
  };

  // -----------------------------------------------------------------------
  // Group findings
  // -----------------------------------------------------------------------

  const allFindings = employeeResults.flatMap((er) => er.findings);

  const groupedFindings = (() => {
    if (groupBy === "employee") {
      const groups: Record<string, Finding[]> = {};
      for (const er of employeeResults) {
        groups[er.employee] = er.findings;
      }
      return groups;
    }
    // severity
    const groups: Record<string, Finding[]> = {};
    for (const f of allFindings) {
      const key = f.severity;
      if (!groups[key]) groups[key] = [];
      groups[key].push(f);
    }
    return groups;
  })();

  // -----------------------------------------------------------------------
  // Export
  // -----------------------------------------------------------------------

  const exportMarkdown = () => {
    if (!summary) return;
    const lines: string[] = [
      `# Audit Report: ${summary.url}`,
      `**Mode:** ${summary.mode}  \n**Total Findings:** ${summary.total_findings}`,
      "",
      "## Summary by Severity",
      ...Object.entries(summary.by_severity)
        .filter(([, c]) => c > 0)
        .map(([s, c]) => `- **${s}**: ${c}`),
      "",
    ];

    for (const er of employeeResults) {
      if (er.findings.length === 0) continue;
      lines.push(`## ${er.emoji} ${er.employee} (${er.findings_count} findings)`);
      for (const f of er.findings) {
        const sev = SEVERITY_CONFIG[f.severity];
        lines.push(
          `### ${sev?.label ?? f.severity}: ${f.title}`,
          `- **Category:** ${f.category}`,
          `- **Description:** ${f.description}`,
          `- **Fix:** ${f.fix_suggestion}`,
          ...(f.evidence_snippet ? [`- **Evidence:** \`${f.evidence_snippet}\``] : []),
          ...(f.wcag_criterion ? [`- **WCAG:** ${f.wcag_criterion}`] : []),
          ...(f.score_impact ? [`- **Impact:** ${f.score_impact}`] : []),
          "",
        );
      }
    }

    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `audit-${new URL(summary.url).hostname}-${Date.now()}.md`;
    a.click();
  };

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header: URL input + actions */}
      <div className="shrink-0 border-b border-white/10 p-4">
        <div className="flex items-center gap-3">
          <div className="flex flex-1 items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 focus-within:border-blue-500/50">
            <Search className="h-4 w-4 text-muted-foreground shrink-0" />
            <input
              type="url"
              placeholder="Enter webapp URL to audit..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runAudit("quick")}
              disabled={running}
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/50"
            />
          </div>

          {running ? (
            <Button variant="outline" size="sm" onClick={handleCancel} className="shrink-0">
              <XCircle className="mr-1 h-4 w-4" /> Cancel
            </Button>
          ) : (
            <>
              <Button size="sm" onClick={() => runAudit("quick")} disabled={!url.trim()} className="shrink-0">
                <Zap className="mr-1 h-4 w-4" /> Quick Scan
              </Button>
              <Button size="sm" onClick={() => runAudit("full")} disabled={!url.trim()} className="shrink-0">
                <Shield className="mr-1 h-4 w-4" /> Full Audit
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Progress bar */}
      {running && (
        <div className="shrink-0 border-b border-white/10 px-4 py-2">
          <div className="flex items-center gap-3 text-sm">
            <Loader2 className="h-4 w-4 animate-spin text-blue-400" />
            <span className="text-muted-foreground">
              {phase === "collecting"
                ? "Collecting page data..."
                : `Analyzing (${employeeResults.length}/6 employees done)...`}
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/10">
            <motion.div
              className="h-full bg-blue-500"
              initial={{ width: 0 }}
              animate={{ width: `${(employeeResults.length / 6) * 100}%` }}
              transition={{ duration: 0.3 }}
            />
          </div>
        </div>
      )}

      {/* Summary banner */}
      {summary && (
        <div className="shrink-0 border-b border-white/10 bg-white/5 px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <span className="text-sm font-medium">{summary.url}</span>
              <div className="flex items-center gap-2">
                {Object.entries(summary.by_severity)
                  .filter(([, count]) => count > 0)
                  .map(([severity, count]) => {
                    const cfg = SEVERITY_CONFIG[severity as keyof typeof SEVERITY_CONFIG];
                    if (!cfg) return null;
                    const Icon = cfg.icon;
                    return (
                      <span key={severity} className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs ${cfg.bg} ${cfg.color}`}>
                        <Icon className="h-3 w-3" /> {count}
                      </span>
                    );
                  })}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex overflow-hidden rounded-lg border border-white/10">
                <button
                  onClick={() => setGroupBy("employee")}
                  className={`px-2 py-1 text-xs ${groupBy === "employee" ? "bg-white/10" : ""}`}
                >
                  By Employee
                </button>
                <button
                  onClick={() => setGroupBy("severity")}
                  className={`px-2 py-1 text-xs ${groupBy === "severity" ? "bg-white/10" : ""}`}
                >
                  By Severity
                </button>
              </div>
              <Button variant="ghost" size="sm" onClick={exportMarkdown}>
                <Download className="mr-1 h-4 w-4" /> Export
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Findings */}
      <div className="flex-1 overflow-y-auto p-4">
        {!summary && !running && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Enter a URL and run an audit to see findings.
          </div>
        )}

        <AnimatePresence mode="wait">
          {Object.keys(groupedFindings).length > 0 && (
            <motion.div
              key={groupBy}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="space-y-6"
            >
              {Object.entries(groupedFindings).map(([group, findings]) => (
                <motion.div
                  key={group}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="space-y-2"
                >
                  {/* Group header */}
                  <div className="flex items-center gap-2 text-sm">
                    {groupBy === "employee" && (
                      <span className="text-base">{EMPLOYEE_EMOJI[group] ?? "🤖"}</span>
                    )}
                    <span className="font-medium">{group}</span>
                    <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs text-muted-foreground">
                      {findings.length}
                    </span>
                  </div>

                  {/* Finding cards */}
                  <div className="space-y-1.5">
                    {findings.map((finding) => {
                      const severity = SEVERITY_CONFIG[finding.severity] ?? SEVERITY_CONFIG.info;
                      const SevIcon = severity.icon;
                      const isExpanded = expandedCards.has(finding.id);

                      return (
                        <motion.div
                          key={finding.id}
                          initial={{ opacity: 0, x: -8 }}
                          animate={{ opacity: 1, x: 0 }}
                          className={`rounded-lg border ${severity.border} ${severity.bg} cursor-pointer transition-colors hover:bg-white/5`}
                          onClick={() => toggleCard(finding.id)}
                        >
                          {/* Compact row */}
                          <div className="flex items-start gap-2 p-3">
                            <SevIcon className={`mt-0.5 h-4 w-4 shrink-0 ${severity.color}`} />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium">{finding.title}</span>
                                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${severity.bg} ${severity.color}`}>
                                  {finding.category}
                                </span>
                                {finding.wcag_criterion && (
                                  <span className="shrink-0 rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] text-purple-400">
                                    WCAG {finding.wcag_criterion}
                                  </span>
                                )}
                              </div>
                              {!isExpanded && (
                                <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                                  {finding.description}
                                </p>
                              )}
                            </div>
                            <ChevronRight
                              className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${isExpanded ? "rotate-90" : ""}`}
                            />
                          </div>

                          {/* Expanded detail */}
                          <AnimatePresence>
                            {isExpanded && (
                              <motion.div
                                initial={{ height: 0, opacity: 0 }}
                                animate={{ height: "auto", opacity: 1 }}
                                exit={{ height: 0, opacity: 0 }}
                                transition={{ duration: 0.15 }}
                                className="overflow-hidden"
                              >
                                <div className="space-y-2 border-t border-white/5 px-3 pb-3 pt-2">
                                  <div>
                                    <span className="text-[10px] font-medium uppercase text-muted-foreground">Description</span>
                                    <p className="text-sm">{finding.description}</p>
                                  </div>
                                  <div>
                                    <span className="text-[10px] font-medium uppercase text-muted-foreground">Fix Suggestion</span>
                                    <p className="text-sm">{finding.fix_suggestion}</p>
                                  </div>
                                  {finding.evidence_snippet && (
                                    <div>
                                      <span className="text-[10px] font-medium uppercase text-muted-foreground">Evidence</span>
                                      <pre className="mt-1 overflow-x-auto rounded bg-black/30 p-2 text-xs text-muted-foreground">
                                        {finding.evidence_snippet}
                                      </pre>
                                    </div>
                                  )}
                                  {finding.score_impact && (
                                    <div className="flex items-center gap-1 text-xs">
                                      <Activity className="h-3 w-3 text-green-400" />
                                      <span className="text-green-400">{finding.score_impact}</span>
                                    </div>
                                  )}
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </motion.div>
                      );
                    })}
                  </div>
                </motion.div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
