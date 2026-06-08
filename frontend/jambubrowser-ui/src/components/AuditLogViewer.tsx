import { useState, useEffect } from "react";
import { localFetch } from "../utils/api";

interface AuditEntry {
  id: number;
  timestamp: number;
  category: string;
  action: string;
  details: Record<string, any>;
  actor: string;
  session_id: string | null;
  hash: string;
}

interface AuditStats {
  total_entries: number;
  categories: Record<string, number>;
  chain_valid: boolean;
  oldest_entry: number | null;
  newest_entry: number | null;
}

export function AuditLogViewer() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>("all");
  const [limit, setLimit] = useState(50);

  useEffect(() => {
    fetchAuditData();
  }, [selectedCategory, limit]);

  const fetchAuditData = async () => {
    try {
      setLoading(true);
      
      // Fetch stats
      const statsResponse = await localFetch("/audit/stats");
      const statsData = await statsResponse.json();
      setStats(statsData);
      
      // Fetch entries
      const entriesResponse = await localFetch(
        `/audit/log?limit=${limit}${selectedCategory !== "all" ? `&category=${selectedCategory}` : ""}`
      );
      const entriesData = await entriesResponse.json();
      setEntries(entriesData.entries || []);
      
      setError(null);
    } catch (err) {
      setError("Failed to fetch audit data");
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const formatTimestamp = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleString();
  };

  const formatDetails = (details: Record<string, any>) => {
    return Object.entries(details)
      .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`)
      .join(", ");
  };

  if (loading) {
    return (
      <div className="audit-log-viewer glass">
        <h3>Audit Log</h3>
        <p>Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="audit-log-viewer glass">
        <h3>Audit Log</h3>
        <p className="error">{error}</p>
        <button onClick={fetchAuditData}>Retry</button>
      </div>
    );
  }

  return (
    <div className="audit-log-viewer glass">
      <h3>Audit Log</h3>
      
      {stats && (
        <div className="audit-stats">
          <div className="stat">
            <span className="label">Total Entries:</span>
            <span className="value">{stats.total_entries}</span>
          </div>
          <div className="stat">
            <span className="label">Chain Valid:</span>
            <span className={`value ${stats.chain_valid ? 'secure' : 'error'}`}>
              {stats.chain_valid ? 'Yes' : 'No'}
            </span>
          </div>
          <div className="stat">
            <span className="label">Categories:</span>
            <span className="value">{Object.keys(stats.categories).length}</span>
          </div>
        </div>
      )}

      <div className="controls">
        <label>
          Category:
          <select 
            value={selectedCategory} 
            onChange={(e) => setSelectedCategory(e.target.value)}
          >
            <option value="all">All</option>
            <option value="research">Research</option>
            <option value="browser">Browser</option>
            <option value="credential">Credential</option>
            <option value="network">Network</option>
            <option value="privacy">Privacy</option>
            <option value="system">System</option>
            <option value="error">Error</option>
          </select>
        </label>
        
        <label>
          Limit:
          <input 
            type="number" 
            value={limit} 
            onChange={(e) => setLimit(parseInt(e.target.value) || 50)}
            min={10}
            max={500}
          />
        </label>
        
        <button onClick={fetchAuditData} className="refresh-btn">
          Refresh
        </button>
      </div>

      <div className="entries-list">
        {entries.length === 0 ? (
          <p>No audit entries found.</p>
        ) : (
          entries.map((entry) => (
            <div key={entry.id} className="audit-entry">
              <div className="entry-header">
                <span className="entry-id">#{entry.id}</span>
                <span className="entry-time">{formatTimestamp(entry.timestamp)}</span>
                <span className={`entry-category ${entry.category}`}>{entry.category}</span>
              </div>
              <div className="entry-action">{entry.action}</div>
              <div className="entry-details">{formatDetails(entry.details)}</div>
              {entry.session_id && (
                <div className="entry-session">Session: {entry.session_id}</div>
              )}
              <div className="entry-hash">Hash: {entry.hash.substring(0, 16)}...</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
