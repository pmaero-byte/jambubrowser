import { useState } from "react";
import { AppShell } from "./components/layout/AppShell";
import { Sidebar } from "./components/layout/Sidebar";
import { ChatPane } from "./components/chat/ChatPane";
import { BrowserPane } from "./components/browser/BrowserPane";
import { Card, CardContent, CardHeader, CardTitle } from "./components/ui/card";

export default function App() {
  const [activePanel, setActivePanel] = useState("chat");
  type Message = { role: "user" | "assistant"; content: string };
  const [messages, setMessages] = useState<Message[]>([
    { role: "assistant", content: "Welcome to Jambubrowser. What should I research?" },
  ]);
  const [browserUrl, setBrowserUrl] = useState("https://example.com");
  const [loading, setLoading] = useState(false);

  const handleSend = async (text: string) => {
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);
    // Placeholder for backend integration
    await new Promise((r) => setTimeout(r, 800));
    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: `I received: "${text}". Backend integration pending.` },
    ]);
    setLoading(false);
  };

  const mainContent =
    activePanel === "browser" ? (
      <BrowserPane url={browserUrl} onNavigate={setBrowserUrl} />
    ) : (
      <ChatPane messages={messages} onSend={handleSend} isLoading={loading} />
    );

  const inspector = (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-sm">Inspector</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        Active panel: <span className="font-medium text-foreground">{activePanel}</span>
        <div className="mt-4">Contextual details will appear here.</div>
      </CardContent>
    </Card>
  );

  return (
    <AppShell
      sidebar={<Sidebar activePanel={activePanel} onChangePanel={setActivePanel} />}
      inspector={inspector}
    >
      {mainContent}
    </AppShell>
  );
}
