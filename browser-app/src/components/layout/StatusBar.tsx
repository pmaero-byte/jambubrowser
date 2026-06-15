import { Cpu, Wifi, Lock, DollarSign } from "lucide-react";

export function StatusBar() {
  return (
    <footer className="flex h-8 items-center justify-between border-t border-border bg-card px-3 text-xs text-muted-foreground">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1">
          <Wifi className="h-3 w-3" />
          <span>Local mode</span>
        </div>
        <div className="flex items-center gap-1">
          <Cpu className="h-3 w-3" />
          <span>MPS</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1">
          <Lock className="h-3 w-3" />
          <span>Vault locked</span>
        </div>
        <div className="flex items-center gap-1">
          <DollarSign className="h-3 w-3" />
          <span>$0.00</span>
        </div>
      </div>
    </footer>
  );
}
