import { useState, useRef, useCallback, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Search, Zap, Shield, Activity, ChevronRight,
  Download, Loader2, AlertTriangle, CheckCircle,
  Info, AlertOctagon, XCircle, Share2, FileText,
  ExternalLink, Clock, X, ChevronDown, FileJson,
} from "lucide-react";
import { Button } from "../ui/button";
import { localFetch, localFetchStream, engineOrigin } from "../../utils/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BusinessImpact {
  user_impact: string;
  revenue_impact: string;
  fix_effort: string;
  priority_score: number;
  reasoning: string;
}

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
  business_impact?: BusinessImpact;
  code_fix?: string;
  fix_group?: string;
}

interface ProductContext {
  what_it_does?: string;
  target_audience?: string;
  value_proposition?: string;
  key_features?: string[];
  tech_stack?: string[];
  business_model?: string;
}

interface FixGroup {
  id: string;
  title: string;
  description: string;
  finding_count: number;
  total_impact: string;
  fix_effort: string;
  code_fix: string;
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
  audit_id?: number | null;
  product_context?: ProductContext;
  fix_groups?: FixGroup[];
  findings?: Finding[];
}

interface AuditRecord {
  id: number;
  url: string;
  title: string;
  mode: string;
  total_findings: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  info_count: number;
  created_at: number | null;
  share_token: string | null;
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

function formatHistoryDate(epochSeconds: number | null): string {
  if (!epochSeconds) return "—";
  try {
    return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AuditPanel() {
  const [url, setUrl] = useState("");
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<string>("idle");
  const [employeeResults, setEmployeeResults] = useState<EmployeeResult[]>([]);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [productContext, setProductContext] = useState<ProductContext | null>(null);
  const [fixGroups, setFixGroups] = useState<FixGroup[]>([]);
  const [groupBy, setGroupBy] = useState<GroupBy>("employee");
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);

  // Audit history / report / share / export state
  const [auditId, setAuditId] = useState<number | null>(null);
  const [history, setHistory] = useState<AuditRecord[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [report, setReport] = useState<{ title: string; html: string } | null>(null);

  const reset = () => {
    setEmployeeResults([]);
    setProductContext(null);
    setFixGroups([]);
    setSummary(null);
    setPhase("idle");
    setAuditId(null);
    setShareUrl(null);
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
        const res = await localFetchStream(
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
      case "product_context":
        setProductContext(data);
        break;
      case "done":
        setSummary(data);
        setAuditId(data.audit_id ?? null);
        if (data.product_context) setProductContext(data.product_context);
        if (data.fix_groups) setFixGroups(data.fix_groups);
        setPhase("done");
        loadHistory();
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
  // History / report / share / export
  // -----------------------------------------------------------------------

  const loadHistory = useCallback(async () => {
    try {
      const res = await localFetch("/audit/history?limit=10");
      const data = await res.json();
      setHistory(data.audits || []);
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const openReport = useCallback(async (id: number, url: string, title?: string) => {
    try {
      const res = await localFetch(`/audit/report/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setReport({ title: title || url, html: await res.text() });
    } catch (e) {
      console.error("Report failed:", e);
    }
  }, []);

  const shareAudit = useCallback(async (id: number) => {
    try {
      const res = await localFetch(`/audit/history/${id}/share`, { method: "POST" });
      const data = await res.json();
      const link = `${engineOrigin()}${data.share_url}/report`;
      setShareUrl(link);
      try {
        await navigator.clipboard.writeText(link);
      } catch {
        /* clipboard denied — the link stays visible for manual copy */
      }
      await loadHistory();
    } catch (e) {
      console.error("Share failed:", e);
    }
  }, [loadHistory]);

  const downloadExport = useCallback(
    async (id: number, format: "sarif" | "json" | "markdown") => {
      setExportOpen(false);
      try {
        const res = await localFetch(`/audit/export/${format}?audit_id=${id}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `jambu-audit-${id}.${format === "markdown" ? "md" : format}`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        console.error("Export failed:", e);
      }
    },
    [],
  );

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

              {auditId != null ? (
                <>
                  <Button variant="ghost" size="sm" onClick={() => openReport(auditId, summary.url)}>
                    <FileText className="mr-1 h-4 w-4" /> Report
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => shareAudit(auditId)}>
                    <Share2 className="mr-1 h-4 w-4" /> Share
                  </Button>
                  <div className="relative">
                    <Button variant="ghost" size="sm" onClick={() => setExportOpen((v) => !v)}>
                      <Download className="mr-1 h-4 w-4" /> Export
                      <ChevronDown className="ml-1 h-3 w-3" />
                    </Button>
                    <AnimatePresence>
                      {exportOpen && (
                        <>
                          <div className="fixed inset-0 z-40" onClick={() => setExportOpen(false)} />
                          <motion.div
                            initial={{ opacity: 0, y: -4, scale: 0.98 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: -4, scale: 0.98 }}
                            transition={{ duration: 0.12 }}
                            className="absolute right-0 top-full z-50 mt-1 w-48 overflow-hidden rounded-lg border border-white/10 bg-zinc-900 shadow-xl"
                          >
                            {([
                              ["sarif", "SARIF (code scanning)"],
                              ["json", "Canonical JSON"],
                              ["markdown", "Markdown"],
                            ] as const).map(([format, label]) => (
                              <button
                                key={format}
                                onClick={() => downloadExport(auditId, format)}
                                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-white/10"
                              >
                                <FileJson className="h-3 w-3 text-muted-foreground" /> {label}
                              </button>
                            ))}
                          </motion.div>
                        </>
                      )}
                    </AnimatePresence>
                  </div>
                </>
              ) : (
                <Button variant="ghost" size="sm" onClick={exportMarkdown}>
                  <Download className="mr-1 h-4 w-4" /> Export
                </Button>
              )}
            </div>
          </div>

        </div>
      )}

      {/* Share link strip — shown for shares from history or the run banner */}
      {shareUrl && (
        <div className="shrink-0 border-b border-white/10 bg-blue-500/10 px-4 py-2">
          <div className="flex items-center gap-2 text-xs">
            <Share2 className="h-3 w-3 shrink-0 text-blue-400" />
            <span className="shrink-0 text-muted-foreground">Share link (copied):</span>
            <a
              href={shareUrl}
              target="_blank"
              rel="noreferrer"
              className="truncate text-blue-400 hover:underline"
              title={shareUrl}
            >
              {shareUrl}
            </a>
            <button
              onClick={() => setShareUrl(null)}
              className="ml-auto shrink-0 text-muted-foreground/60 hover:text-foreground"
              aria-label="Dismiss share link"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        </div>
      )}

      {/* Product Context */}
      {productContext && productContext.what_it_does && (
        <div className="shrink-0 border-b border-white/10 bg-blue-500/5 px-4 py-3">
          <div className="flex items-start gap-3">
            <span className="text-lg">🎯</span>
            <div className="flex-1">
              <div className="text-sm font-medium mb-1">Product Context</div>
              <div className="text-xs text-muted-foreground space-y-1">
                <div><strong>What it does:</strong> {productContext.what_it_does}</div>
                {productContext.target_audience && <div><strong>Audience:</strong> {productContext.target_audience}</div>}
                {productContext.value_proposition && <div><strong>Value prop:</strong> {productContext.value_proposition}</div>}
                {productContext.business_model && <div><strong>Business model:</strong> {productContext.business_model}</div>}
                {productContext.tech_stack && productContext.tech_stack.length > 0 && (
                  <div><strong>Tech stack:</strong> {productContext.tech_stack.join(", ")}</div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Fix Groups */}
      {fixGroups.length > 0 && (
        <div className="shrink-0 border-b border-white/10 px-4 py-3">
          <div className="text-xs font-medium mb-2">Fix Groups ({fixGroups.length})</div>
          <div className="flex flex-wrap gap-2">
            {fixGroups.map((fg) => (
              <button
                key={fg.id}
                onClick={() => {
                  const groupFindings = summary?.findings?.filter(f => f.fix_group === fg.id) || [];
                  setExpandedCards(new Set(groupFindings.map(f => f.id)));
                }}
                className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs hover:bg-white/10 transition-colors"
              >
                <span className={`w-2 h-2 rounded-full ${
                  fg.total_impact === "critical" ? "bg-red-500" :
                  fg.total_impact === "high" ? "bg-orange-500" :
                  fg.total_impact === "medium" ? "bg-yellow-500" :
                  "bg-blue-500"
                }`} />
                <span className="font-medium">{fg.title}</span>
                <span className="text-muted-foreground">({fg.finding_count})</span>
                <span className="text-muted-foreground">· {fg.fix_effort}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Recent audits */}
      {history.length > 0 && (
        <div className="shrink-0 border-b border-white/10">
          <button
            onClick={() => setHistoryOpen((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2 text-xs hover:bg-white/5"
          >
            <Clock className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="font-medium">Recent audits</span>
            <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-muted-foreground">
              {history.length}
            </span>
            <ChevronRight
              className={`ml-auto h-3.5 w-3.5 text-muted-foreground transition-transform ${historyOpen ? "rotate-90" : ""}`}
            />
          </button>
          <AnimatePresence initial={false}>
            {historyOpen && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="overflow-hidden"
              >
                <div className="max-h-56 space-y-1 overflow-y-auto px-4 pb-3">
                  {history.map((rec) => (
                    <div
                      key={rec.id}
                      className="flex items-center gap-2 rounded-lg border border-white/5 bg-white/5 px-2.5 py-1.5 text-xs"
                    >
                      <span className="w-24 shrink-0 text-muted-foreground">
                        {formatHistoryDate(rec.created_at)}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-medium" title={rec.url}>
                        {rec.url}
                      </span>
                      <span className="shrink-0 text-muted-foreground">{rec.mode}</span>
                      {rec.critical_count > 0 && (
                        <span className="shrink-0 rounded bg-red-500/10 px-1.5 py-0.5 text-red-400">
                          {rec.critical_count} crit
                        </span>
                      )}
                      <span className="shrink-0 text-muted-foreground">
                        {rec.total_findings} findings
                      </span>
                      <div className="flex shrink-0 items-center gap-0.5">
                        <Button
                          variant="ghost" size="icon" className="h-6 w-6"
                          onClick={() => openReport(rec.id, rec.url, rec.title)}
                          aria-label={`Report for audit ${rec.id}`}
                          title="View HTML report"
                        >
                          <FileText className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost" size="icon" className="h-6 w-6"
                          onClick={() => shareAudit(rec.id)}
                          aria-label={`Share audit ${rec.id}`}
                          title="Create share link"
                        >
                          <Share2 className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost" size="icon" className="h-6 w-6"
                          onClick={() => downloadExport(rec.id, "sarif")}
                          aria-label={`Download SARIF for audit ${rec.id}`}
                          title="Download SARIF"
                        >
                          <Download className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Findings */}
      <div className="flex-1 overflow-y-auto p-4">
        {!summary && !running && (
          <div className="flex h-full flex-col items-center justify-center gap-6">
            <div className="text-5xl">🔍</div>
            <div className="text-center">
              <h3 className="text-lg font-medium mb-2">Audit any webapp</h3>
              <p className="text-sm text-muted-foreground max-w-md">
                Enter a URL above and run Quick Scan (3 employees, ~90s) or Full Audit
                (6 employees, ~3min) to find security, performance, and UX issues.
              </p>
            </div>
            <div className="flex gap-3">
              <div className="flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1.5 text-xs">
                <span>🔒</span> Security
              </div>
              <div className="flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1.5 text-xs">
                <span>⚡</span> Performance
              </div>
              <div className="flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1.5 text-xs">
                <span>🎨</span> UX/UI
              </div>
            </div>
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

                                  {finding.business_impact && (
                                    <div className="rounded-lg bg-blue-500/10 p-2 space-y-1">
                                      <span className="text-[10px] font-medium uppercase text-blue-400">Business Impact</span>
                                      <div className="text-xs space-y-0.5">
                                        <div><strong>Users affected:</strong> {finding.business_impact.user_impact}</div>
                                        <div><strong>Revenue impact:</strong> {finding.business_impact.revenue_impact}</div>
                                        <div><strong>Fix effort:</strong> {finding.business_impact.fix_effort}</div>
                                        <div><strong>Priority:</strong> {finding.business_impact.priority_score}/10 — {finding.business_impact.reasoning}</div>
                                      </div>
                                    </div>
                                  )}

                                  <div>
                                    <span className="text-[10px] font-medium uppercase text-muted-foreground">Fix Suggestion</span>
                                    <p className="text-sm">{finding.fix_suggestion}</p>
                                  </div>

                                  {finding.code_fix && (
                                    <div>
                                      <span className="text-[10px] font-medium uppercase text-green-400">Code Fix</span>
                                      <pre className="mt-1 overflow-x-auto rounded bg-green-500/10 p-2 text-xs text-green-300 whitespace-pre-wrap">
                                        {finding.code_fix}
                                      </pre>
                                    </div>
                                  )}

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

      {/* HTML report modal */}
      <AnimatePresence>
        {report && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
            onClick={() => setReport(null)}
          >
            <motion.div
              initial={{ scale: 0.97, y: 8 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.97, y: 8 }}
              transition={{ duration: 0.15 }}
              className="flex h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-950 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2.5">
                <FileText className="h-4 w-4 shrink-0 text-blue-400" />
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{report.title}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    const blob = new Blob([report.html], { type: "text/html" });
                    const url = URL.createObjectURL(blob);
                    window.open(url, "_blank");
                    setTimeout(() => URL.revokeObjectURL(url), 60_000);
                  }}
                >
                  <ExternalLink className="mr-1 h-3.5 w-3.5" /> Open
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => setReport(null)}
                  aria-label="Close report"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
              {/* sandbox="" — the report is static HTML with no scripts */}
              <iframe
                title="Audit report"
                srcDoc={report.html}
                sandbox=""
                className="h-full w-full bg-white"
              />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
