import { Cpu, Wifi, Lock, Unlock, DollarSign, Activity } from "lucide-react";
import { motion } from "motion/react";
import { useAgentWebSocket } from "../../utils/useAgentWebSocket";
import { useAppStore } from "../../store/appStore";

/**
 * TokenFlow — 5 small bars whose heights oscillate to suggest live token
 * generation. Speed scales with the real `tokens_per_sec` from telemetry
 * (capped), so when the agent is fast the bars wiggle fast; when it pauses
 * they still gently breathe so the status bar never feels dead.
 */
function TokenFlow({ tokensPerSec }: { tokensPerSec: number | undefined }) {
  const active = (tokensPerSec ?? 0) > 0;
  // Map [0, 50] tok/s -> [2.2s, 0.4s] period. Clamp so a 1000 tok/s burst
  // doesn't make the bars look frantic.
  const tps = Math.max(0, Math.min(tokensPerSec ?? 0, 50));
  const period = active ? 2.2 - (tps / 50) * 1.8 : 3.5;
  const bars = [0, 1, 2, 3, 4];
  return (
    <div className="flex h-3 w-8 items-end gap-[1px]" aria-hidden>
      {bars.map((i) => (
        <motion.span
          key={i}
          className={
            "block w-[2px] rounded-sm " +
            (active ? "bg-emerald-400" : "bg-muted-foreground/40")
          }
          animate={{ height: ["30%", "90%", "40%", "70%", "30%"] }}
          transition={{
            duration: period,
            repeat: Infinity,
            ease: "easeInOut",
            delay: i * 0.08,
          }}
        />
      ))}
    </div>
  );
}

export function StatusBar() {
  const { telemetry, connected, agentState } = useAgentWebSocket();
  const { privacyMode } = useAppStore();

  const modeLabels: Record<typeof privacyMode, string> = {
    standard: "Standard",
    enhanced: "Enhanced",
    maximum: "Maximum",
    local_only: "Local Only",
  };

  return (
    <footer className="h-8 border-t border-border bg-card px-3 flex items-center justify-between text-xs text-muted-foreground shrink-0">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-2 w-2">
            <motion.span
              className={`absolute inline-flex h-full w-full rounded-full ${
                connected ? "bg-emerald-400" : "bg-red-400"
              }`}
              animate={
                connected
                  ? { scale: [1, 2.4], opacity: [0.6, 0] }
                  : { scale: 1, opacity: 1 }
              }
              transition={{ duration: 1.4, repeat: connected ? Infinity : 0, ease: "easeOut" }}
            />
            <span
              className={`relative inline-flex h-2 w-2 rounded-full ${
                connected ? "bg-emerald-400" : "bg-red-400"
              }`}
            />
          </span>
          <Wifi className={`h-3 w-3 ${connected ? "text-emerald-400" : "text-red-400"}`} />
          <span>{connected ? "WS live" : "WS offline"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Cpu className="h-3 w-3" />
          <span>{telemetry?.model || "idle"}</span>
        </div>
        <div className="hidden sm:flex items-center gap-1.5">
          <TokenFlow tokensPerSec={telemetry?.tokens_per_sec} />
          <Activity className="h-3 w-3" />
          <span>{telemetry?.tokens_per_sec?.toFixed(1) || "0.0"} tok/s</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          {privacyMode === "local_only" ? <Lock className="h-3 w-3 text-cyan-400" /> : <Unlock className="h-3 w-3" />}
          <span>{modeLabels[privacyMode]}</span>
        </div>
        <div className="hidden sm:flex items-center gap-1.5">
          <DollarSign className="h-3 w-3" />
          <span>$0.00</span>
        </div>
        <div className="hidden md:flex items-center gap-1.5 capitalize">
          <span className="text-accent">{agentState?.state || "idle"}</span>
          {agentState?.zone && <span className="text-muted-foreground">in {agentState.zone}</span>}
        </div>
      </div>
    </footer>
  );
}
