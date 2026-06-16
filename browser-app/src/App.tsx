import { useCallback, useState } from "react";
import { AppShell } from "./components/layout/AppShell";
import { ChatPane } from "./components/chat/ChatPane";
import { BrowserPane } from "./components/browser/BrowserPane";
import { PrivacyControls } from "./components/privacy/PrivacyControls";
import { AuditLogViewer } from "./components/audit/AuditLogViewer";
import { VaultUnlock } from "./components/vault/VaultUnlock";
import { MemoryPanel } from "./components/memory/MemoryPanel";
import { InspectorPanel } from "./components/inspector/InspectorPanel";
import { CommandPalette } from "./components/command/CommandPalette";
import { OnboardingWizard } from "./components/onboarding/OnboardingWizard";
import { useAppStore } from "./store/appStore";
import { useAgentWebSocket } from "./utils/useAgentWebSocket";
import { runAgentStream } from "./utils/agent";
import { localFetch } from "./utils/api";
import type { AgentEvent } from "./utils/types";

const USER_ID = "default";

export default function App() {
  const {
    activeTab,
    addMessage,
    updateLastMessage,
    setIsLoading,
    activeModel,
    onboardingOpen,
    setOnboardingOpen,
  } = useAppStore();

  const { clearReasoning } = useAgentWebSocket();
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      setIsLoading(true);
      setAgentEvents([]);
      clearReasoning();
      addMessage({
        id: crypto.randomUUID(),
        role: "user",
        content: text,
      });
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
      });

      try {
        if (activeModel === "legacy") {
          const res = await localFetch("/research", {
            method: "POST",
            body: JSON.stringify({
              query: text,
              brain_only: true,
            }),
          });
          const data = await res.json();
          updateLastMessage({
            content: data.answer || "",
            sources: data.sources || [],
          });
        } else {
          let answer = "";
          let sources: string[] = [];
          let lastRunCompleted: AgentEvent | null = null;

          for await (const ev of runAgentStream({
            query: text,
            user_id: USER_ID,
            max_steps: 10,
          })) {
            setAgentEvents((prev) => [...prev, ev]);
            if (ev.type === "answer_ready") {
              answer = ev.data.answer || "";
              sources = ev.data.sources || [];
            }
            if (ev.type === "run_completed") {
              lastRunCompleted = ev;
            }
          }

          updateLastMessage({
            content: answer,
            sources,
            agentRun: lastRunCompleted?.data,
          });
        }
      } catch (err: any) {
        console.error(err);
        const msg =
          err?.name === "AbortError"
            ? "Research timed out."
            : "Sorry, I encountered an error processing your request.";
        updateLastMessage({ content: msg });
      } finally {
        setIsLoading(false);
      }
    },
    [activeModel, addMessage, updateLastMessage, setIsLoading, clearReasoning]
  );

  const renderCanvas = () => {
    switch (activeTab) {
      case "chat":
      case "plan":
        return <ChatPane agentEvents={agentEvents} onSend={handleSend} onStop={() => {}} />;
      case "browser":
        return <BrowserPane />;
      case "logs":
        return <AuditLogViewer />;
      case "memory":
      case "knowledge":
        return <MemoryPanel />;
      case "missions":
        return (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Missions scheduler coming soon.
          </div>
        );
      case "history":
        return (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Session history coming soon.
          </div>
        );
      case "privacy":
        return <PrivacyControls />;
      case "audit":
        return <AuditLogViewer />;
      case "vault":
        return <VaultUnlock />;
      case "settings":
        return (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Settings coming soon.
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <>
      <AppShell inspector={<InspectorPanel />}>
        <div className="flex h-full flex-col overflow-hidden">{renderCanvas()}</div>
      </AppShell>
      <CommandPalette />
      <OnboardingWizard forceOpen={onboardingOpen} onClose={() => setOnboardingOpen(false)} />
    </>
  );
}
