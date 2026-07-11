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
    <header className="h-14 border-b border-border/50 glass flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2.5">
          <div className="h-7 w-7 rounded-lg gradient-brand flex items-center justify-center text-white shadow-glow">
            <Bot size={16} strokeWidth={2.5} />
          </div>
          <span className="font-semibold text-sm tracking-tight">Jambubrowser</span>
        </div>
        <div className="h-4 w-px bg-border/50 mx-2 hidden sm:block" />
        <button className="hidden sm:flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-all duration-200 hover:bg-muted/50 rounded-md px-2 py-1">
          <span className="font-medium text-foreground">Workspace</span>
          <span>Default</span>
        </button>
      </div>

      <div className="flex items-center gap-2">
        <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-muted/60 text-xs transition-colors duration-200">
          <span className="text-muted-foreground">Model:</span>
          <span className="font-medium text-foreground">{activeModel}</span>
        </div>

        <div
          className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all duration-300 ${
            privacyMode === "local_only"
              ? "border-cyan-400/30 text-cyan-400 bg-cyan-400/10 shadow-[0_0_12px_oklch(0.7_0.15_200/15%)]"
              : privacyMode === "maximum"
                ? "border-accent/30 text-accent bg-accent/10 glow-accent"
                : "border-border/50 text-muted-foreground hover:border-border hover:bg-muted/30"
          }`}
        >
          <Shield size={12} />
          <span>{modeLabels[privacyMode]}</span>
        </div>

        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 text-xs h-8 border-border/50 hover:border-border hover:bg-muted/50 transition-all duration-200"
          onClick={() => setCommandOpen(true)}
        >
          <Command size={13} />
          <span className="hidden sm:inline">Command</span>
          <kbd className="hidden sm:inline-flex items-center gap-0.5 px-1 rounded bg-muted/60 border border-border/50 text-[10px] text-muted-foreground">
            ⌘K
          </kbd>
        </Button>

        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-all duration-200">
          <Lock size={15} />
        </Button>
      </div>
    </header>
  );
}
