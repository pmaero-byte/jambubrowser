import { Button } from "../ui/button";
import { cn } from "../../lib/utils";
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
  Activity,
  Sparkles,
  FileText,
} from "lucide-react";
import { useAppStore, type CanvasTab } from "../../store/appStore";

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
  { id: "privacy", label: "Privacy", icon: Shield },
  { id: "audit", label: "Audit", icon: ScrollText },
  { id: "vault", label: "Vault", icon: Wallet },
  { id: "settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const { activeTab, setActiveTab } = useAppStore();

  return (
    <nav className="flex h-full flex-col gap-1 overflow-y-auto p-2" aria-label="Primary">
      <div className="mb-2 px-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        Workspace
      </div>
      {workspaceNav.map((item) => {
        const Icon = item.icon;
        const active = activeTab === item.id;
        return (
          <Button
            key={item.id}
            variant={active ? "secondary" : "ghost"}
            className={cn("justify-start gap-3 h-9", active && "bg-muted")}
            onClick={() => setActiveTab(item.id)}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className="truncate">{item.label}</span>
          </Button>
        );
      })}

      <div className="mt-4 mb-2 px-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        System
      </div>
      {systemNav.map((item) => {
        const Icon = item.icon;
        const active = activeTab === item.id;
        return (
          <Button
            key={item.id}
            variant={active ? "secondary" : "ghost"}
            className={cn("justify-start gap-3 h-9", active && "bg-muted")}
            onClick={() => setActiveTab(item.id)}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className="truncate">{item.label}</span>
          </Button>
        );
      })}

      <div className="mt-auto flex items-center gap-2 px-2 pt-4 text-xs text-muted-foreground">
        <Activity className="h-3 w-3 text-emerald-400" />
        <span>Engine online</span>
      </div>
    </nav>
  );
}
