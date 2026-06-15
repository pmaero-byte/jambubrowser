import { Button } from "../ui/button";
import { Menu, PanelRight, Command, Shield, Lock } from "lucide-react";

interface TopBarProps {
  onToggleSidebar: () => void;
  onToggleInspector: () => void;
}

export function TopBar({ onToggleSidebar, onToggleInspector }: TopBarProps) {
  return (
    <header className="flex h-12 items-center justify-between border-b border-border bg-card px-3">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="Toggle sidebar">
          <Menu className="h-5 w-5" />
        </Button>
        <span className="font-semibold tracking-tight">Jambubrowser</span>
        <span className="ml-2 rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
          v3.3
        </span>
      </div>

      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" className="gap-2">
          <Command className="h-4 w-4" />
          <span>⌘K</span>
        </Button>
        <Button variant="ghost" size="icon" aria-label="Privacy">
          <Shield className="h-5 w-5" />
        </Button>
        <Button variant="ghost" size="icon" aria-label="Vault">
          <Lock className="h-5 w-5" />
        </Button>
        <Button variant="ghost" size="icon" onClick={onToggleInspector} aria-label="Toggle inspector">
          <PanelRight className="h-5 w-5" />
        </Button>
      </div>
    </header>
  );
}
