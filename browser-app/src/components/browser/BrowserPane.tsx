import { useState } from "react";
import { Button } from "../ui/button";
import { RotateCw, ArrowLeft, ArrowRight } from "lucide-react";

interface BrowserPaneProps {
  url: string;
  onNavigate: (url: string) => void;
}

export function BrowserPane({ url, onNavigate }: BrowserPaneProps) {
  const [value, setValue] = useState(url);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border p-2">
        <Button variant="ghost" size="icon" aria-label="Back">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" aria-label="Forward">
          <ArrowRight className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" aria-label="Reload">
          <RotateCw className="h-4 w-4" />
        </Button>
        <form
          className="flex flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            onNavigate(value);
          }}
        >
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
        </form>
      </div>
      <iframe
        src={url}
        sandbox="allow-scripts allow-forms allow-popups"
        className="min-h-0 flex-1 bg-white"
        title="Browser"
      />
    </div>
  );
}
