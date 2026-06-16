import { useState } from "react";
import { Lock, Unlock, Loader2 } from "lucide-react";
import { Button } from "../ui/button";
import { localFetch } from "../../utils/api";

export function VaultUnlock() {
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [message, setMessage] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) return;
    setStatus("loading");
    try {
      const r = await localFetch("/vault/unlock", {
        method: "POST",
        body: JSON.stringify({ master_password: password }),
      });
      const data = await r.json();
      if (data.success) {
        setStatus("success");
        setMessage("Vault unlocked.");
        setPassword("");
      } else {
        setStatus("error");
        setMessage(data.error || "Unlock failed.");
      }
    } catch {
      setStatus("error");
      setMessage("Network error.");
    }
  };

  return (
    <div className="p-4">
      <div className="mb-4 flex items-center gap-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Lock size={16} />
        </div>
        <h2 className="text-lg font-semibold">Credential Vault</h2>
      </div>

      <p className="mb-4 text-sm text-muted-foreground">
        Unlock the AES-256 encrypted credential vault to auto-fill forms and access stored domains.
      </p>

      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="block text-xs font-medium text-muted-foreground">
          Master Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setStatus("idle");
            setMessage("");
          }}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
          placeholder="••••••••"
        />
        <Button type="submit" className="w-full gap-2" disabled={status === "loading" || !password}>
          {status === "loading" ? <Loader2 size={14} className="animate-spin" /> : status === "success" ? <Unlock size={14} /> : <Lock size={14} />}
          {status === "loading" ? "Unlocking…" : "Unlock Vault"}
        </Button>
      </form>

      {message && (
        <div
          className={`mt-3 rounded-md px-3 py-2 text-xs ${
            status === "success"
              ? "bg-emerald-400/10 text-emerald-400"
              : "bg-red-400/10 text-red-400"
          }`}
        >
          {message}
        </div>
      )}
    </div>
  );
}
