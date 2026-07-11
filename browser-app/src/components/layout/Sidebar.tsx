import { Button } from "../ui/button";
import { cn } from "../../lib/utils";
import { motion } from "motion/react";
import {
  MessageSquare,
  Globe,
  Brain,
  ScrollText,
  Settings,
  History,
  FolderKanban,
  Shield,
  Wallet,
  Sparkles,
  Users,
  FileText,
  Puzzle,
} from "lucide-react";
import { useAppStore, type CanvasTab } from "../../store/appStore";
import { useAgentWebSocket } from "../../utils/useAgentWebSocket";

const workspaceNav: { id: CanvasTab; label: string; icon: React.ElementType }[] = [
  { id: "chat", label: "Research", icon: MessageSquare },
  { id: "plan", label: "Plan", icon: Sparkles },
  { id: "browser", label: "Browser", icon: Globe },
  { id: "logs", label: "Logs", icon: FileText },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "knowledge", label: "Knowledge", icon: Brain },
  { id: "missions", label: "Missions", icon: FolderKanban },
  { id: "history", label: "History", icon: History },
];

const systemNav: { id: CanvasTab; label: string; icon: React.ElementType }[] = [
  { id: "extensions", label: "Extensions", icon: Puzzle },
  { id: "privacy", label: "Privacy", icon: Shield },
  { id: "audit", label: "Audit", icon: ScrollText },
  { id: "team", label: "Team", icon: Users },
  { id: "vault", label: "Vault", icon: Wallet },
  { id: "settings", label: "Settings", icon: Settings },
];

function NavItem({
  label,
  Icon,
  active,
  agentRunning,
  onClick,
}: {
  label: string;
  Icon: React.ElementType;
  active: boolean;
  agentRunning: boolean;
  onClick: () => void;
}) {
  const pulsing = active && agentRunning;
  return (
    <Button
      variant={active ? "secondary" : "ghost"}
      className={cn(
        "relative justify-start gap-3 h-9 overflow-hidden transition-all duration-200",
        active && "bg-muted/80 glow-accent",
        !active && "hover:bg-muted/50 hover:text-foreground",
      )}
      onClick={onClick}
    >
      {pulsing && (
        <motion.span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-md ring-1 ring-primary/30"
          animate={{ opacity: [0.4, 0.9, 0.4] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
      <motion.span
        className="relative shrink-0"
        animate={pulsing ? { scale: [1, 1.12, 1] } : { scale: 1 }}
        transition={{ duration: 1.2, repeat: pulsing ? Infinity : 0, ease: "easeInOut" }}
      >
        <Icon className={cn("h-4 w-4 transition-colors duration-200", pulsing && "text-primary", active && "text-foreground")} />
      </motion.span>
      <span className="relative truncate text-sm">{label}</span>
      {pulsing && (
        <span className="relative ml-auto flex h-2 w-2">
          <motion.span
            className="absolute inline-flex h-full w-full rounded-full bg-emerald-400"
            animate={{ scale: [1, 2.2], opacity: [0.6, 0] }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeOut" }}
          />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
        </span>
      )}
    </Button>
  );
}

export function Sidebar() {
  const { activeTab, setActiveTab } = useAppStore();
  const { agentState } = useAgentWebSocket();
  const agentRunning = !!agentState && agentState.state !== "idle";

  return (
    <nav className="flex h-full flex-col gap-1 overflow-y-auto p-2" aria-label="Primary">
      <div className="mb-2 px-2 text-[10px] font-semibold text-muted-foreground/60 uppercase tracking-widest">
        Workspace
      </div>
      {workspaceNav.map((item) => (
        <NavItem
          key={item.id}
          label={item.label}
          Icon={item.icon}
          active={activeTab === item.id}
          agentRunning={agentRunning}
          onClick={() => setActiveTab(item.id)}
        />
      ))}

      <div className="my-3 mx-2 h-px bg-gradient-to-r from-transparent via-border/50 to-transparent" />

      <div className="mb-2 px-2 text-[10px] font-semibold text-muted-foreground/60 uppercase tracking-widest">
        System
      </div>
      {systemNav.map((item) => (
        <NavItem
          key={item.id}
          label={item.label}
          Icon={item.icon}
          active={activeTab === item.id}
          agentRunning={agentRunning}
          onClick={() => setActiveTab(item.id)}
        />
      ))}

      <div className="mt-auto flex items-center gap-2 px-2 pt-4 text-xs text-muted-foreground/60">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400/60 animate-ping" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
        </span>
        <span>Engine online</span>
      </div>
    </nav>
  );
}
