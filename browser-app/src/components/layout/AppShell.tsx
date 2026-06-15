import { ReactNode, useState } from "react";
import { motion } from "framer-motion";
import { TopBar } from "./TopBar";
import { StatusBar } from "./StatusBar";

interface AppShellProps {
  children: ReactNode;
  sidebar?: ReactNode;
  inspector?: ReactNode;
}

export function AppShell({ children, sidebar, inspector }: AppShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <TopBar
        onToggleSidebar={() => setSidebarOpen((s) => !s)}
        onToggleInspector={() => setInspectorOpen((s) => !s)}
      />
      <div className="flex min-h-0 flex-1">
        <motion.aside
          initial={false}
          animate={{ width: sidebarOpen ? 260 : 0, opacity: sidebarOpen ? 1 : 0 }}
          transition={{ type: "spring", stiffness: 400, damping: 30 }}
          className="flex flex-col overflow-hidden border-r border-border bg-card"
        >
          <div className="w-[260px] flex-1 overflow-y-auto p-3">{sidebar}</div>
        </motion.aside>

        <main className="flex min-w-0 flex-1 flex-col bg-background">
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
              {children}
            </div>
            <motion.aside
              initial={false}
              animate={{ width: inspectorOpen ? 320 : 0, opacity: inspectorOpen ? 1 : 0 }}
              transition={{ type: "spring", stiffness: 400, damping: 30 }}
              className="flex flex-col overflow-hidden border-l border-border bg-card"
            >
              <div className="w-[320px] flex-1 overflow-y-auto p-3">{inspector}</div>
            </motion.aside>
          </div>
        </main>
      </div>
      <StatusBar />
    </div>
  );
}
