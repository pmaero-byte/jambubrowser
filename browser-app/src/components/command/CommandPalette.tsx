import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "cmdk";
import {
  MessageSquare,
  Globe,
  Brain,
  ScrollText,
  Settings,
  FolderKanban,
  Shield,
  Wallet,
  FileText,
  Sparkles,
  Zap,
  Lock,
  Unlock,
  Moon,
  Sun,
  HelpCircle,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { useAppStore, type CanvasTab } from "../../store/appStore";
import { useAgentWebSocket } from "../../utils/useAgentWebSocket";
import { useEffect } from "react";

const navActions: { id: CanvasTab; label: string; icon: React.ElementType; shortcut?: string }[] = [
  { id: "chat", label: "Research Chat", icon: MessageSquare, shortcut: "Meta+1" },
  { id: "plan", label: "Plan Surface", icon: Sparkles },
  { id: "browser", label: "Browser", icon: Globe, shortcut: "Meta+T" },
  { id: "logs", label: "Logs / Audit", icon: FileText, shortcut: "Meta+L" },
  { id: "memory", label: "Memory", icon: Brain, shortcut: "Meta+Shift+M" },
  { id: "knowledge", label: "Knowledge Graph", icon: Brain },
  { id: "missions", label: "Missions", icon: FolderKanban },
  { id: "privacy", label: "Privacy Controls", icon: Shield, shortcut: "Meta+Shift+P" },
  { id: "audit", label: "Audit Log", icon: ScrollText },
  { id: "vault", label: "Vault", icon: Wallet },
  { id: "settings", label: "Settings", icon: Settings },
];

export function CommandPalette() {
  const {
    commandOpen,
    setCommandOpen,
    setActiveTab,
    addBrowserTab,
    setOnboardingOpen,
    toggleSidebar,
    toggleInspector,
  } = useAppStore();
  const { clearReasoning } = useAgentWebSocket();

  useEffect(() => {
    if (commandOpen) {
      const t = setTimeout(() => {
        document.querySelector<HTMLInputElement>("[cmdk-input]")?.focus();
      }, 50);
      return () => clearTimeout(t);
    }
  }, [commandOpen]);

  return (
    <>
      <AnimatePresence>
        {commandOpen && (
          // Backdrop: blur + dark tint, fades in / out. Sits *behind* the
          // cmdk dialog content (which is portaled on top by Radix).
          <motion.div
            key="cmd-backdrop"
            aria-hidden
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
          />
        )}
      </AnimatePresence>
      <CommandDialog
        open={commandOpen}
        onOpenChange={setCommandOpen}
        aria-label="Command palette"
        role="dialog"
      >
        <CommandInput placeholder="Type a command or search..." aria-label="Command input" />
        <CommandList className="max-h-[60vh] overflow-y-auto">
          <CommandEmpty>No results found.</CommandEmpty>

          <CommandGroup heading="Navigate">
            {navActions.map((action) => {
              const Icon = action.icon;
              return (
                <CommandItem
                  key={action.id}
                  onSelect={() => {
                    if (action.id === "browser") addBrowserTab();
                    else setActiveTab(action.id);
                    setCommandOpen(false);
                  }}
                >
                  <Icon className="mr-2 h-4 w-4" />
                  <span>{action.label}</span>
                  {action.shortcut && (
                    <kbd className="ml-auto rounded bg-muted px-1 text-[10px]">
                      {action.shortcut.replace("Meta", "⌘").replace("Shift", "⇧")}
                    </kbd>
                  )}
                </CommandItem>
              );
            })}
          </CommandGroup>

          <CommandSeparator />

          <CommandGroup heading="Actions">
            <CommandItem onSelect={() => { toggleSidebar(); setCommandOpen(false); }}>
              <Zap className="mr-2 h-4 w-4" /> Toggle sidebar
            </CommandItem>
            <CommandItem onSelect={() => { toggleInspector(); setCommandOpen(false); }}>
              <Zap className="mr-2 h-4 w-4" /> Toggle inspector
            </CommandItem>
            <CommandItem onSelect={() => { clearReasoning(); setCommandOpen(false); }}>
              <Unlock className="mr-2 h-4 w-4" /> Clear reasoning trace
            </CommandItem>
            <CommandItem onSelect={() => { setOnboardingOpen(true); setCommandOpen(false); }}>
              <HelpCircle className="mr-2 h-4 w-4" /> Open onboarding
            </CommandItem>
          </CommandGroup>

          <CommandSeparator />

          <CommandGroup heading="Preferences">
            <CommandItem onSelect={() => { document.documentElement.classList.toggle("dark"); setCommandOpen(false); }}>
              <Moon className="mr-2 h-4 w-4" /> Toggle dark mode
            </CommandItem>
            <CommandItem onSelect={() => { setCommandOpen(false); }}>
              <Lock className="mr-2 h-4 w-4" /> Lock vault
            </CommandItem>
            <CommandItem onSelect={() => { setActiveTab("privacy"); setCommandOpen(false); }}>
              <Sun className="mr-2 h-4 w-4" /> Change privacy mode
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </>
  );
}
