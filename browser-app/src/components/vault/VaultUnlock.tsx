import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Lock, Unlock, Loader2, Check } from "lucide-react";
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

  // The lock badge in the header opens/closes based on the run state, giving
  // the user a kinetic confirmation of what just happened.
  const badgeOpen = status === "success";
  const isError = status === "error";
  const isLoading = status === "loading";

  return (
    <div className="p-4">
      <div className="mb-4 flex items-center gap-2">
        <motion.div
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary"
          animate={
            badgeOpen
              ? { rotate: [0, -10, 8, 0], scale: [1, 1.12, 1] }
              : isLoading
                ? { scale: [1, 1.06, 1] }
                : { rotate: 0, scale: 1 }
          }
          transition={
            isLoading
              ? { duration: 1.2, repeat: Infinity, ease: "easeInOut" }
              : { duration: 0.45, ease: "easeOut" }
          }
        >
          {badgeOpen ? <Unlock size={16} /> : <Lock size={16} />}
        </motion.div>
        <h2 className="text-lg font-semibold">Credential Vault</h2>
      </div>

      <p className="mb-4 text-sm text-muted-foreground">
        Unlock the AES-256 encrypted credential vault to auto-fill forms and access stored domains.
      </p>

      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="block text-xs font-medium text-muted-foreground">
          Master Password
        </label>
        {/* Shake on error: short horizontal keyframes (4-shake pattern) */}
        <motion.input
          type="password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setStatus("idle");
            setMessage("");
          }}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
          placeholder="••••••••"
          animate={
            isError
              ? { x: [0, -8, 8, -6, 6, -3, 3, 0] }
              : { x: 0 }
          }
          transition={{ duration: 0.4 }}
        />
        <Button type="submit" className="w-full gap-2" disabled={isLoading || !password}>
          <AnimatePresence mode="wait" initial={false}>
            {isLoading ? (
              <motion.span
                key="loading"
                initial={{ opacity: 0, scale: 0.6 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.6 }}
                transition={{ duration: 0.15 }}
              >
                <Loader2 size={14} className="animate-spin" />
              </motion.span>
            ) : status === "success" ? (
              <motion.span
                key="success"
                initial={{ opacity: 0, scale: 0.4, rotate: -90 }}
                animate={{ opacity: 1, scale: 1, rotate: 0 }}
                transition={{ type: "spring", stiffness: 380, damping: 18 }}
              >
                <Check size={14} />
              </motion.span>
            ) : (
              <motion.span
                key="idle"
                initial={{ opacity: 0, scale: 0.6 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.6 }}
                transition={{ duration: 0.15 }}
              >
                <Lock size={14} />
              </motion.span>
            )}
          </AnimatePresence>
          {isLoading ? "Unlocking…" : status === "success" ? "Unlocked" : "Unlock Vault"}
        </Button>
      </form>

      <AnimatePresence>
        {message && (
          <motion.div
            key="msg"
            initial={{ opacity: 0, y: -6, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -6, height: 0 }}
            transition={{ duration: 0.18 }}
            className={`overflow-hidden`}
          >
            <div
              className={`mt-3 rounded-md px-3 py-2 text-xs ${
                status === "success"
                  ? "bg-emerald-400/10 text-emerald-400"
                  : "bg-red-400/10 text-red-400"
              }`}
            >
              {message}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
