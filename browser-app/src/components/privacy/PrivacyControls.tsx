import { useState, useEffect } from "react";
import { Shield, AlertTriangle, Check } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";
import { useAppStore } from "../../store/appStore";

const MODE_INFO: Record<string, { label: string; desc: string; warns: string | null }> = {
  standard: {
    label: "Standard",
    desc: "No restrictions. All engines, all LLMs, all URLs allowed.",
    warns: null,
  },
  enhanced: {
    label: "Enhanced",
    desc: "PII redacted from stored content. Tracking IDs stripped from scraped text.",
    warns: null,
  },
  maximum: {
    label: "Maximum",
    desc: "PII + URL redaction. Known tracking domains blocked. External calls may fail silently.",
    warns: "Web search and agent research may return thin or no results.",
  },
  local_only: {
    label: "Local Only",
    desc: "Zero network access. Only local LLM + local knowledge vault. No search, no scraping.",
    warns: "Agent cannot search the web. Cloud LLMs blocked. Most features require local model setup.",
  },
};

export function PrivacyControls() {
  const { setPrivacyMode } = useAppStore();
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [settingMode, setSettingMode] = useState(false);
  const [pendingMode, setPendingMode] = useState<string | null>(null);

  useEffect(() => {
    fetchPrivacyReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchPrivacyReport = async () => {
    try {
      setLoading(true);
      const response = await localFetch("/privacy/report");
      const data = await response.json();
      setReport(data);
      setPrivacyMode(data?.privacy?.mode || "enhanced");
      setError(null);
    } catch (err) {
      setError("Failed to fetch privacy report");
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const confirmSetMode = async () => {
    if (!pendingMode) return;
    await setMode(pendingMode);
    setPendingMode(null);
  };

  const setMode = async (mode: string) => {
    try {
      setSettingMode(true);
      await localFetch("/privacy/mode", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      setPrivacyMode(mode as any);
      await fetchPrivacyReport();
    } catch (err) {
      console.error(err);
    } finally {
      setSettingMode(false);
    }
  };

  const handleModeClick = (mode: string) => {
    const info = MODE_INFO[mode];
    if (info?.warns) {
      setPendingMode(mode);
    } else {
      setMode(mode);
    }
  };

  if (loading) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        <Shield className="mb-2 h-5 w-5" /> Loading privacy report…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-sm text-red-400">
        <p>{error}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={fetchPrivacyReport}>
          Retry
        </Button>
      </div>
    );
  }

  const privacy = report?.privacy || {};
  const audit = report?.audit || {};
  const vaultStatus = report?.vault_status || "unknown";

  return (
    <div className="p-4 space-y-5">
      <h2 className="text-lg font-semibold">Privacy Controls</h2>

      {pendingMode && (
        <div className="rounded-xl border border-border bg-card p-3">
          <div className="flex items-start gap-2 text-amber-400">
            <AlertTriangle size={16} />
            <p className="text-sm">{MODE_INFO[pendingMode]?.warns}</p>
          </div>
          <div className="mt-3 flex gap-2">
            <Button size="sm" onClick={confirmSetMode} disabled={settingMode}>
              {settingMode ? "Applying…" : "Confirm"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setPendingMode(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Privacy Mode
        </h3>
        <div className="grid grid-cols-2 gap-2">
          {["standard", "enhanced", "maximum", "local_only"].map((mode) => {
            const info = MODE_INFO[mode];
            const isActive = privacy.mode === mode;
            return (
              <button
                key={mode}
                onClick={() => handleModeClick(mode)}
                disabled={settingMode}
                className={`rounded-lg border p-3 text-left text-xs transition-colors ${
                  isActive
                    ? "border-accent bg-accent/10 text-foreground"
                    : "border-border bg-card text-muted-foreground hover:bg-muted"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{info.label}</span>
                  {isActive && <Check size={12} className="text-accent" />}
                </div>
                <p className="mt-1 text-[10px] leading-snug opacity-80">{info.desc}</p>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Protection Status
        </h3>
        <ul className="space-y-1.5 text-sm">
          <li className="flex justify-between">
            <span className="text-muted-foreground">Local Only</span>
            <span className={privacy.local_only ? "text-emerald-400" : "text-amber-400"}>
              {privacy.local_only ? "Yes" : "No"}
            </span>
          </li>
          <li className="flex justify-between">
            <span className="text-muted-foreground">PII Removal</span>
            <span className={privacy.pii_removal ? "text-emerald-400" : "text-amber-400"}>
              {privacy.pii_removal ? "Enabled" : "Disabled"}
            </span>
          </li>
          <li className="flex justify-between">
            <span className="text-muted-foreground">Tracking Blocked</span>
            <span className={privacy.tracking_blocked ? "text-emerald-400" : "text-amber-400"}>
              {privacy.tracking_blocked ? "Yes" : "No"}
            </span>
          </li>
          <li className="flex justify-between">
            <span className="text-muted-foreground">PII Detections</span>
            <span>{privacy.audit_statistics?.pii_detections || 0}</span>
          </li>
          <li className="flex justify-between">
            <span className="text-muted-foreground">Blocked Requests</span>
            <span>{privacy.audit_statistics?.blocked_requests || 0}</span>
          </li>
        </ul>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Credential Vault
        </h3>
        <p className="text-sm">
          Status:{" "}
          <span className={vaultStatus === "locked" ? "text-emerald-400" : "text-amber-400"}>
            {vaultStatus === "locked" ? "Locked" : "Unlocked"}
          </span>
        </p>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Audit Log
        </h3>
        <ul className="space-y-1.5 text-sm">
          <li className="flex justify-between">
            <span className="text-muted-foreground">Total Entries</span>
            <span>{audit.total_entries || 0}</span>
          </li>
          <li className="flex justify-between">
            <span className="text-muted-foreground">Retention</span>
            <span>{audit.retention_days || 90} days</span>
          </li>
        </ul>
      </section>

      <Button variant="outline" size="sm" className="w-full" onClick={fetchPrivacyReport}>
        Refresh Report
      </Button>
    </div>
  );
}
