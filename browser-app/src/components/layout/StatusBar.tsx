import { Cpu, Wifi, Lock, Unlock, DollarSign, Activity } from "lucide-react";
import { useAgentWebSocket } from "../../utils/useAgentWebSocket";
import { useAppStore } from "../../store/appStore";

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
          <Wifi className={`h-3 w-3 ${connected ? "text-emerald-400" : "text-red-400"}`} />
          <span>{connected ? "WS live" : "WS offline"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Cpu className="h-3 w-3" />
          <span>{telemetry?.model || "idle"}</span>
        </div>
        <div className="hidden sm:flex items-center gap-1.5">
          <Activity className="h-3 w-3" />
          <span>
            {telemetry?.tokens_per_sec?.toFixed(1) || "0.0"} tok/s
          </span>
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
