import { useState } from "react";
import { localFetch } from "../utils/api";

export function VaultUnlock() {
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [loading, setLoading] = useState(false);

  const handleUnlock = async () => {
    setLoading(true);
    setStatus(null);
    try {
      const res = await localFetch("/vault/unlock", {
        method: "POST",
        body: JSON.stringify({ master_password: password }),
      });
      const data = await res.json();
      if (data.success) {
        setStatus({ type: "success", message: "Vault unlocked" });
        setPassword("");
      } else {
        setStatus({ type: "error", message: data.error || "Failed to unlock" });
      }
    } catch (err) {
      setStatus({ type: "error", message: "Connection error" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="vault-unlock">
      <h3>Credential Vault</h3>
      <p style={{ color: "var(--text-dim)", fontSize: "0.85rem" }}>
        Enter master password to unlock the credential vault.
      </p>
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Master password"
        onKeyDown={(e) => e.key === "Enter" && handleUnlock()}
      />
      <button className="action-btn" onClick={handleUnlock} disabled={loading || !password}>
        {loading ? "Unlocking..." : "Unlock Vault"}
      </button>
      {status && (
        <div className={status.type}>{status.message}</div>
      )}
    </div>
  );
}
