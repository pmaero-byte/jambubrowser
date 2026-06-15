import { useState } from "react";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Send, Paperclip, Mic } from "lucide-react";

interface ChatPaneProps {
  messages: Array<{ role: "user" | "assistant"; content: string }>;
  onSend: (text: string) => void;
  isLoading?: boolean;
}

export function ChatPane({ messages, onSend, isLoading }: ChatPaneProps) {
  const [input, setInput] = useState("");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
        {messages.map((m, i) => (
          <Card key={i} className={m.role === "user" ? "ml-8 bg-secondary" : "mr-8"}>
            <CardContent className="p-3">
              <div className="text-xs font-medium text-muted-foreground mb-1">
                {m.role === "user" ? "You" : "Jambu"}
              </div>
              <div className="whitespace-pre-wrap text-sm">{m.content}</div>
            </CardContent>
          </Card>
        ))}
        {isLoading && (
          <Card className="mr-8">
            <CardContent className="p-3">
              <div className="h-4 w-32 animate-pulse rounded bg-muted" />
            </CardContent>
          </Card>
        )}
      </div>

      <form
        className="flex items-center gap-2 border-t border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!input.trim()) return;
          onSend(input);
          setInput("");
        }}
      >
        <Button type="button" variant="ghost" size="icon" aria-label="Attach">
          <Paperclip className="h-5 w-5" />
        </Button>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask Jambu to research, browse, or remember..."
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
        />
        <Button type="button" variant="ghost" size="icon" aria-label="Voice">
          <Mic className="h-5 w-5" />
        </Button>
        <Button type="submit" size="icon" disabled={isLoading || !input.trim()}>
          <Send className="h-5 w-5" />
        </Button>
      </form>
    </div>
  );
}
