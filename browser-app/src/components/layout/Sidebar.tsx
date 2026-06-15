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
} from "lucide-react";

interface SidebarProps {
  activePanel: string;
  onChangePanel: (panel: string) => void;
}

const nav = [
  { id: "chat", label: "Research", icon: MessageSquare },
  { id: "browser", label: "Browser", icon: Globe },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "audit", label: "Audit Log", icon: ScrollText },
  { id: "missions", label: "Missions", icon: FolderKanban },
  { id: "history", label: "History", icon: History },
  { id: "privacy", label: "Privacy", icon: Shield },
  { id: "vault", label: "Vault", icon: Wallet },
  { id: "settings", label: "Settings", icon: Settings },
];

export function Sidebar({ activePanel, onChangePanel }: SidebarProps) {
  return (
    <nav className="flex flex-col gap-1">
      <div className="mb-2 px-2 text-xs font-medium text-muted-foreground">Workspace</div>
      {nav.slice(0, 6).map((item) => {
        const Icon = item.icon;
        const active = activePanel === item.id;
        return (
          <Button
            key={item.id}
            variant={active ? "secondary" : "ghost"}
            className={cn("justify-start gap-3", active && "bg-muted")}
            onClick={() => onChangePanel(item.id)}
          >
            <Icon className="h-4 w-4" />
            <span>{item.label}</span>
          </Button>
        );
      })}
      <div className="mt-4 mb-2 px-2 text-xs font-medium text-muted-foreground">System</div>
      {nav.slice(6).map((item) => {
        const Icon = item.icon;
        const active = activePanel === item.id;
        return (
          <Button
            key={item.id}
            variant={active ? "secondary" : "ghost"}
            className={cn("justify-start gap-3", active && "bg-muted")}
            onClick={() => onChangePanel(item.id)}
          >
            <Icon className="h-4 w-4" />
            <span>{item.label}</span>
          </Button>
        );
      })}
      <div className="mt-auto flex items-center gap-2 px-2 pt-4 text-xs text-muted-foreground">
        <Activity className="h-3 w-3" />
        <span>Engine online</span>
      </div>
    </nav>
  );
}
