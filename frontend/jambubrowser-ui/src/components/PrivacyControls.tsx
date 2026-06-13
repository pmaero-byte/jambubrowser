import { useState, useEffect } from "react";
import { localFetch } from "../utils/api";

const MODE_INFO: Record<string, { label: string; desc: string; warns: string | null }> = {
  standard: {
    label: "Standard",
    desc: "No restrictions. All engines, all LLMs, all URLs allowed.",
    warns: null,
  },
  enhanced: {
    label: "Enhanced",
    desc: "PII redacted from stored content. Tracking IDs stripped from scraped text. Headers cleaned.",
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

interface PrivacyControlsProps {
  refreshKey?: number;
}

export function PrivacyControls({ refreshKey }: PrivacyControlsProps) {
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [settingMode, setSettingMode] = useState(false);
  const [pendingMode, setPendingMode] = useState<string | null>(null);

  useEffect(() => {
    fetchPrivacyReport();
  }, [refreshKey]);

  const fetchPrivacyReport = async () => {
    try {
      setLoading(true);
      const response = await localFetch("/privacy/report");
      const data = await response.json();
      setReport(data);
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
    try {
      setSettingMode(true);
      await localFetch("/privacy/mode", {
        method: "POST",
        body: JSON.stringify({ mode: pendingMode }),
      });
      setPendingMode(null);
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
      setPrivacyModeDirect(mode);
    }
  };

  const setPrivacyModeDirect = async (mode: string) => {
    try {
      setSettingMode(true);
      await localFetch("/privacy/mode", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      await fetchPrivacyReport();
    } catch (err) {
      console.error(err);
    } finally {
      setSettingMode(false);
    }
  };

  if (loading) {
    return (
      <div className="privacy-controls glass">
        <h3>Privacy Controls</h3>
        <p>Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="privacy-controls glass">
        <h3>Privacy Controls</h3>
        <p className="error">{error}</p>
        <button onClick={fetchPrivacyReport}>Retry</button>
      </div>
    );
  }

  const privacy = report?.privacy || {};
  const audit = report?.audit || {};
  const vaultStatus = report?.vault_status || "unknown";

  return (
    <div className="privacy-controls glass">
      <h3>Privacy Controls</h3>

      {/* Confirmation dialog for restrictive modes */}
      {pendingMode && (
        <div className="confirm-overlay">
          <div className="confirm-box">
            <h4>Change to {MODE_INFO[pendingMode]?.label}?</h4>
            <p>{MODE_INFO[pendingMode]?.warns}</p>
            <div className="confirm-actions">
              <button className="btn-confirm" onClick={confirmSetMode} disabled={settingMode}>
                {settingMode ? "Applying..." : "Confirm"}
              </button>
              <button className="btn-cancel" onClick={() => setPendingMode(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="status-section">
        <h4>Privacy Mode</h4>
        <div className="mode-selector">
          {["standard", "enhanced", "maximum", "local_only"].map((mode) => {
            const info = MODE_INFO[mode];
            const isActive = privacy.mode === mode;
            return (
              <button
                key={mode}
                className={`mode-btn ${isActive ? "active" : ""}`}
                onClick={() => handleModeClick(mode)}
                disabled={settingMode}
                title={info?.desc}
              >
                {info?.label || mode}
                {isActive && <span className="active-dot" />}
              </button>
            );
          })}
        </div>
        {privacy.mode && MODE_INFO[privacy.mode] && (
          <p className="mode-desc">{MODE_INFO[privacy.mode].desc}</p>
        )}
      </div>

      <div className="status-section">
        <h4>Protection Status</h4>
        <ul>
          <li>
            <span className="label">Local Only:</span>
            <span className={`value ${privacy.local_only ? "secure" : "warning"}`}>
              {privacy.local_only ? "Yes" : "No"}
            </span>
          </li>
          <li>
            <span className="label">PII Removal:</span>
            <span className={`value ${privacy.pii_removal ? "secure" : "warning"}`}>
              {privacy.pii_removal ? "Enabled" : "Disabled"}
            </span>
          </li>
          <li>
            <span className="label">Tracking Blocked:</span>
            <span className={`value ${privacy.tracking_blocked ? "secure" : "warning"}`}>
              {privacy.tracking_blocked ? "Yes" : "No"}
            </span>
          </li>
          <li>
            <span className="label">PII Detections:</span>
            <span className="value">{privacy.audit_statistics?.pii_detections || 0}</span>
          </li>
          <li>
            <span className="label">Blocked Requests:</span>
            <span className="value">{privacy.audit_statistics?.blocked_requests || 0}</span>
          </li>
        </ul>
      </div>

      <div className="status-section">
        <h4>Credential Vault</h4>
        <ul>
          <li>
            <span className="label">Status:</span>
            <span className={`value ${vaultStatus === "locked" ? "secure" : "warning"}`}>
              {vaultStatus === "locked" ? "Locked" : "Unlocked"}
            </span>
          </li>
        </ul>
      </div>

      <div className="status-section">
        <h4>Audit Log</h4>
        <ul>
          <li>
            <span className="label">Total Entries:</span>
            <span className="value">{audit.total_entries || 0}</span>
          </li>
          <li>
            <span className="label">Retention:</span>
            <span className="value">{audit.retention_days || 90} days</span>
          </li>
          {audit.by_category && Object.keys(audit.by_category).length > 0 && (
            <li>
              <span className="label">Categories:</span>
              <span className="value">{Object.keys(audit.by_category).length}</span>
            </li>
          )}
        </ul>
      </div>

      <button onClick={fetchPrivacyReport} className="refresh-btn">
        Refresh Report
      </button>
    </div>
  );
}
