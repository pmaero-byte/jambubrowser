import { useState, useEffect } from "react";
import { localFetch } from "../utils/api";

interface PrivacyReport {
  mode: string;
  network: {
    local_only: boolean;
    external_requests_allowed: boolean;
    blocked_domains_count: number;
  };
  content: {
    pii_detection_enabled: boolean;
    tracking_protection: boolean;
  };
  audit: {
    enabled: boolean;
    chain_valid: boolean;
    total_entries: number;
  };
  vault: {
    locked: boolean;
    credentials_count: number;
  };
}

export function PrivacyControls() {
  const [report, setReport] = useState<PrivacyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPrivacyReport();
  }, []);

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

  return (
    <div className="privacy-controls glass">
      <h3>Privacy Controls</h3>
      
      {report && (
        <>
          <div className="status-section">
            <h4>Network</h4>
            <ul>
              <li>
                <span className="label">Mode:</span>
                <span className={`value ${report.network.local_only ? 'secure' : 'warning'}`}>
                  {report.mode}
                </span>
              </li>
              <li>
                <span className="label">Local Only:</span>
                <span className={`value ${report.network.local_only ? 'secure' : 'warning'}`}>
                  {report.network.local_only ? 'Yes' : 'No'}
                </span>
              </li>
              <li>
                <span className="label">External Requests:</span>
                <span className={`value ${report.network.external_requests_allowed ? 'warning' : 'secure'}`}>
                  {report.network.external_requests_allowed ? 'Allowed' : 'Blocked'}
                </span>
              </li>
              <li>
                <span className="label">Blocked Domains:</span>
                <span className="value">{report.network.blocked_domains_count}</span>
              </li>
            </ul>
          </div>

          <div className="status-section">
            <h4>Content Protection</h4>
            <ul>
              <li>
                <span className="label">PII Detection:</span>
                <span className={`value ${report.content.pii_detection_enabled ? 'secure' : 'warning'}`}>
                  {report.content.pii_detection_enabled ? 'Enabled' : 'Disabled'}
                </span>
              </li>
              <li>
                <span className="label">Tracking Protection:</span>
                <span className={`value ${report.content.tracking_protection ? 'secure' : 'warning'}`}>
                  {report.content.tracking_protection ? 'Enabled' : 'Disabled'}
                </span>
              </li>
            </ul>
          </div>

          <div className="status-section">
            <h4>Audit</h4>
            <ul>
              <li>
                <span className="label">Enabled:</span>
                <span className={`value ${report.audit.enabled ? 'secure' : 'warning'}`}>
                  {report.audit.enabled ? 'Yes' : 'No'}
                </span>
              </li>
              <li>
                <span className="label">Chain Valid:</span>
                <span className={`value ${report.audit.chain_valid ? 'secure' : 'error'}`}>
                  {report.audit.chain_valid ? 'Yes' : 'No'}
                </span>
              </li>
              <li>
                <span className="label">Total Entries:</span>
                <span className="value">{report.audit.total_entries}</span>
              </li>
            </ul>
          </div>

          <div className="status-section">
            <h4>Credential Vault</h4>
            <ul>
              <li>
                <span className="label">Status:</span>
                <span className={`value ${report.vault.locked ? 'secure' : 'warning'}`}>
                  {report.vault.locked ? 'Locked' : 'Unlocked'}
                </span>
              </li>
              <li>
                <span className="label">Credentials:</span>
                <span className="value">{report.vault.credentials_count}</span>
              </li>
            </ul>
          </div>

          <button onClick={fetchPrivacyReport} className="refresh-btn">
            Refresh Report
          </button>
        </>
      )}
    </div>
  );
}
