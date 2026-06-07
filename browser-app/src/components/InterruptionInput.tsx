import { useState } from "react";

interface InterruptionInputProps {
  visible: boolean;
  currentTaskId: string | null;
  clientId: string;
  onSubmit: (instruction: string) => void;
}

export const InterruptionInput = ({ visible, currentTaskId, clientId, onSubmit }: InterruptionInputProps) => {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (inject: boolean) => {
    if (!currentTaskId) return;
    setBusy(true);
    try {
      await fetch(`http://localhost:8001/interrupt/${currentTaskId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          new_instruction: inject ? text.trim() : "",
          client_id: clientId,
        }),
      });
      setText("");
      onSubmit(text);
    } catch (e) {
      console.error("Interrupt failed", e);
    } finally {
      setBusy(false);
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(true);
    }
    if (e.key === "Escape") {
      submit(false);
    }
  };

  return (
    <div className={`interrupt-bar ${visible ? "visible" : ""}`}>
      <div className="interrupt-inner">
        <span className="interrupt-icon">⚡</span>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="Inject new instruction… (Enter to redirect, Esc to cancel)"
          disabled={busy}
        />
        <button className="interrupt-btn primary" onClick={() => submit(true)} disabled={busy || !text.trim()}>
          Redirect
        </button>
        <button className="interrupt-btn" onClick={() => submit(false)} disabled={busy}>
          Stop
        </button>
      </div>
    </div>
  );
};
