import { motion } from "motion/react";
import { TopBar } from "./TopBar";
import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import { useAppStore } from "../../store/appStore";
import { useKeyboardShortcuts } from "../../utils/useKeyboardShortcuts";
import { useCallback } from "react";

interface AppShellProps {
  children: React.ReactNode;
  inspector?: React.ReactNode;
}

export function AppShell({ children, inspector }: AppShellProps) {
  const {
    sidebarOpen,
    inspectorOpen,
    toggleSidebar,
    toggleInspector,
    setActiveTab,
    setCommandOpen,
    setOnboardingOpen,
    addBrowserTab,
  } = useAppStore();

  useKeyboardShortcuts({
    "Meta+B": useCallback(() => toggleSidebar(), [toggleSidebar]),
    "Ctrl+B": useCallback(() => toggleSidebar(), [toggleSidebar]),
    "Meta+K": useCallback(() => setCommandOpen(true), [setCommandOpen]),
    "Ctrl+K": useCallback(() => setCommandOpen(true), [setCommandOpen]),
    "Meta+L": useCallback(() => setActiveTab("logs"), [setActiveTab]),
    "Ctrl+L": useCallback(() => setActiveTab("logs"), [setActiveTab]),
    "Meta+Shift+M": useCallback(() => setActiveTab("memory"), [setActiveTab]),
    "Ctrl+Shift+M": useCallback(() => setActiveTab("memory"), [setActiveTab]),
    "Meta+T": useCallback(() => addBrowserTab(), [addBrowserTab]),
    "Ctrl+T": useCallback(() => addBrowserTab(), [addBrowserTab]),
    "Meta+Shift+P": useCallback(() => setActiveTab("privacy"), [setActiveTab]),
    "Ctrl+Shift+P": useCallback(() => setActiveTab("privacy"), [setActiveTab]),
    "Meta+?": useCallback(() => setOnboardingOpen(true), [setOnboardingOpen]),
    "Ctrl+?": useCallback(() => setOnboardingOpen(true), [setOnboardingOpen]),
    "Meta+\\": useCallback(() => toggleInspector(), [toggleInspector]),
    "Ctrl+\\": useCallback(() => toggleInspector(), [toggleInspector]),
    Escape: useCallback(() => setCommandOpen(false), [setCommandOpen]),
  });

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <TopBar />

      <div className="flex min-h-0 flex-1">
        <motion.aside
          initial={false}
          animate={{ width: sidebarOpen ? 240 : 0, opacity: sidebarOpen ? 1 : 0 }}
          transition={{ type: "spring", stiffness: 400, damping: 30 }}
          className="shrink-0 overflow-hidden border-r border-border bg-card"
          aria-hidden={!sidebarOpen}
        >
          <div className="w-[240px] h-full">
            <Sidebar />
          </div>
        </motion.aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          {children}
        </main>

        <motion.aside
          initial={false}
          animate={{
            width: inspectorOpen ? 320 : 0,
            opacity: inspectorOpen ? 1 : 0,
          }}
          transition={{ type: "spring", stiffness: 400, damping: 30 }}
          className="shrink-0 overflow-hidden border-l border-border bg-card"
          aria-hidden={!inspectorOpen}
        >
          <div className="w-[320px] h-full overflow-hidden">{inspectorOpen && inspector}</div>
        </motion.aside>
      </div>

      <StatusBar />
    </div>
  );
}
