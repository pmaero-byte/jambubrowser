import { Bot, Command, Lock, Shield } from "lucide-react";
import { Button } from "../ui/button";
import { useAppStore } from "../../store/appStore";

export function TopBar() {
  const { setCommandOpen, privacyMode, activeModel } = useAppStore();

  const modeLabels: Record<typeof privacyMode, string> = {
    standard: "Standard",
    enhanced: "Enhanced",
    maximum: "Maximum",
    local_only: "Local Only",
  };

  return (
    <header className="h-14 border-b border-border bg-card/50 backdrop-blur flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="h-7 w-7 rounded-lg bg-primary flex items-center justify-center text-primary-foreground">
            <Bot size={18} />
          </div>
          <span className="font-semibold text-sm tracking-tight">Jambubrowser</span>
        </div>
        <div className="h-4 w-px bg-border mx-2 hidden sm:block" />
        <button className="hidden sm:flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
          <span className="font-medium text-foreground">Workspace</span>
          <span>Default</span>
        </button>
      </div>

      <div className="flex items-center gap-2">
        <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-muted text-xs">
          <span className="text-muted-foreground">Model:</span>
          <span className="font-medium text-foreground">{activeModel}</span>
        </div>

        <div
          className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border ${
            privacyMode === "local_only"
              ? "border-cyan-400/30 text-cyan-400 bg-cyan-400/10"
              : privacyMode === "maximum"
                ? "border-accent/30 text-accent bg-accent/10"
                : "border-border text-muted-foreground"
          }`}
        >
          <Shield size={12} />
          <span>{modeLabels[privacyMode]}</span>
        </div>

        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 text-xs h-8"
          onClick={() => setCommandOpen(true)}
        >
          <Command size={13} />
          <span className="hidden sm:inline">Command</span>
          <kbd className="hidden sm:inline-flex items-center gap-0.5 px-1 rounded bg-muted border border-border text-[10px]">
            ⌘K
          </kbd>
        </Button>

        <Button variant="ghost" size="icon" className="h-8 w-8">
          <Lock size={16} />
        </Button>
      </div>
    </header>
  );
}
