interface TelemetryPanelProps {
  model: string;
  tokensPerSec: number | null;
  currentAction: string;
  reasoningTrace: string;
  filePath: string | null;
  contextSize: number | null;
  totalTokens: number;
  connected: boolean;
}

export const TelemetryPanel = ({
  model,
  tokensPerSec,
  currentAction,
  reasoningTrace,
  filePath,
  contextSize,
  totalTokens,
  connected,
}: TelemetryPanelProps) => {
  const tps = tokensPerSec != null ? tokensPerSec.toFixed(1) : "—";
  const ctx = contextSize != null ? contextSize.toLocaleString() : "—";
  const traceLines = reasoningTrace ? reasoningTrace.split("\n") : [];
  const lastLines = traceLines.slice(-6);

  return (
    <div className="telemetry-panel">
      <div className="tp-header">
        <span className={`tp-conn ${connected ? "on" : "off"}`} />
        <span className="tp-title">TELEMETRY</span>
        <span className="tp-model">{model}</span>
      </div>

      <div className="tp-grid">
        <div className="tp-cell">
          <div className="tp-label">TOK/SEC</div>
          <div className="tp-value accent">{tps}</div>
        </div>
        <div className="tp-cell">
          <div className="tp-label">TOTAL TOK</div>
          <div className="tp-value">{totalTokens.toLocaleString()}</div>
        </div>
        <div className="tp-cell">
          <div className="tp-label">CTX SIZE</div>
          <div className="tp-value">{ctx}</div>
        </div>
      </div>

      <div className="tp-action">
        <div className="tp-label">CURRENT ACTION</div>
        <div className="tp-action-text">{currentAction || "Waiting for input..."}</div>
      </div>

      {filePath && (
        <div className="tp-file">
          <div className="tp-label">FILE</div>
          <div className="tp-file-path" title={filePath}>{filePath}</div>
        </div>
      )}

      <div className="tp-trace">
        <div className="tp-label">REASONING TRACE</div>
        <div className="tp-trace-box">
          {lastLines.length === 0 ? (
            <div className="tp-trace-empty">No trace yet. The LLM's thought stream will appear here when it starts working.</div>
          ) : (
            lastLines.map((line, i) => (
              <div key={i} className="tp-trace-line">
                <span className="tp-trace-gutter">{String(traceLines.length - lastLines.length + i + 1).padStart(3, "0")}</span>
                <span className="tp-trace-text">{line}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
