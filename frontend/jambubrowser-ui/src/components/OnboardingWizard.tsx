import React, { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Trees, ChevronRight, ChevronLeft, Sparkles, Brain, Wrench, Check,
  Server, Zap, MessageSquare, X, Cpu,
} from "lucide-react";
import { localFetch } from "../utils/api";
import { listLLMProviders } from "../utils/agent";
import { addInterest, updateProfile } from "../utils/memory";
import type { LLMProvider } from "../utils/types";

const STORAGE_KEY = "jambu.onboarding.completed.v1";
const STEP_KEY = "jambu.onboarding.step.v1";

interface OnboardingWizardProps {
  /** Force show even if completed (e.g., from "Help" menu) */
  forceOpen?: boolean;
  /** Called when wizard finishes or is skipped */
  onClose?: () => void;
}

const STEPS = [
  { id: "welcome",   title: "Welcome to Jambubrowser",   icon: Trees },
  { id: "provider",  title: "Choose your LLM",           icon: Cpu },
  { id: "memory",    title: "Tell us about you",         icon: Brain },
  { id: "test",      title: "Test the connection",       icon: Zap },
  { id: "done",      title: "You're ready",              icon: Sparkles },
];

export const OnboardingWizard: React.FC<OnboardingWizardProps> = ({ forceOpen, onClose }) => {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [provider, setProvider] = useState<LLMProvider>("auto");
  const [model, setModel] = useState("");
  const [providers, setProviders] = useState<Record<string, string[]>>({});
  const [interests, setInterests] = useState<string[]>([]);
  const [interestDraft, setInterestDraft] = useState("");
  const [workContext, setWorkContext] = useState("");
  const [testQuery, setTestQuery] = useState("Hello, Jambubrowser!");
  const [testResult, setTestResult] = useState<{ content: string; latency_ms: number; model: string; provider: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);

  // Open wizard on first run, or if forceOpen is set
  useEffect(() => {
    if (forceOpen) {
      setOpen(true);
      return;
    }
    const completed = localStorage.getItem(STORAGE_KEY);
    if (!completed) {
      // First run
      setOpen(true);
    }
  }, [forceOpen]);

  // Persist step
  useEffect(() => {
    if (open) {
      localStorage.setItem(STEP_KEY, String(step));
    }
  }, [step, open]);

  // Load providers when step 1 (provider) is shown
  useEffect(() => {
    if (open && step === 1 && Object.keys(providers).length === 0) {
      listLLMProviders()
        .then((d) => setProviders(d.models || {}))
        .catch(() => setProviders({}));
    }
  }, [open, step, providers]);

  const close = useCallback((completed: boolean) => {
    setOpen(false);
    if (completed) {
      localStorage.setItem(STORAGE_KEY, "1");
    }
    onClose?.();
  }, [onClose]);

  const next = useCallback(() => {
    if (step < STEPS.length - 1) {
      setStep((s) => s + 1);
    } else {
      close(true);
    }
  }, [step, close]);

  const back = useCallback(() => {
    if (step > 0) setStep((s) => s - 1);
  }, [step]);

  const skip = useCallback(() => {
    close(false);
  }, [close]);

  const addInterestItem = useCallback(() => {
    const t = interestDraft.trim();
    if (!t) return;
    setInterests((prev) => Array.from(new Set([...prev, t])));
    setInterestDraft("");
  }, [interestDraft]);

  const saveMemory = useCallback(async () => {
    try {
      // Add each interest
      for (const i of interests) {
        await addInterest("default", i);
      }
      if (workContext.trim()) {
        await updateProfile({ user_id: "default", work_context: workContext.trim() });
      }
    } catch {
      // Non-fatal — wizard still continues
    }
  }, [interests, workContext]);

  const runTest = useCallback(async () => {
    setTesting(true);
    setTestError(null);
    setTestResult(null);
    try {
      const body: any = {
        messages: [{ role: "user", content: testQuery }],
      };
      if (provider !== "auto") body.provider = provider;
      if (model) body.model = model;
      const r = await localFetch("/v2/llm/chat", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const data = await r.json();
      setTestResult({
        content: data.content,
        latency_ms: data.latency_ms,
        model: data.model,
        provider: data.provider,
      });
    } catch (e: any) {
      setTestError(e?.message || "Test failed");
    } finally {
      setTesting(false);
    }
  }, [provider, model, testQuery]);

  if (!open) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="onboarding-overlay"
        onClick={(e) => {
          if (e.target === e.currentTarget) skip();
        }}
      >
        <motion.div
          initial={{ scale: 0.95, y: 20 }}
          animate={{ scale: 1, y: 0 }}
          exit={{ scale: 0.95, y: 20 }}
          className="onboarding-modal glass"
        >
          <button className="onboarding-skip" onClick={skip} title="Skip (Esc)">
            <X size={16} />
          </button>

          <div className="onboarding-progress">
            {STEPS.map((s, i) => (
              <div
                key={s.id}
                className={`progress-dot ${i <= step ? "active" : ""} ${i === step ? "current" : ""}`}
                title={s.title}
              />
            ))}
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.2 }}
              className="onboarding-content"
            >
              {/* Step 0: Welcome */}
              {step === 0 && (
                <div className="onboarding-step">
                  <Trees size={48} color="var(--accent)" />
                  <h2>The Sovereign Autonomous Research Agent</h2>
                  <p>
                    Jambubrowser is a fully local, privacy-first AI browser and research engine.
                    It thinks, acts, and evolves entirely on your machine.
                  </p>
                  <ul className="onboarding-bullets">
                    <li><Check size={14} /> 6 LLM providers (Anthropic, OpenAI, Ollama, MLX, MiniMax, Mock)</li>
                    <li><Check size={14} /> ReAct agent loop with verification &amp; replanning</li>
                    <li><Check size={14} /> Persistent memory that learns your preferences</li>
                    <li><Check size={14} /> Encrypted vault, audit log, fingerprint rotation</li>
                    <li><Check size={14} /> Zero data leaves your machine in Local-Only mode</li>
                  </ul>
                </div>
              )}

              {/* Step 1: Provider */}
              {step === 1 && (
                <div className="onboarding-step">
                  <Cpu size={36} color="var(--accent)" />
                  <h2>Choose your LLM</h2>
                  <p>Pick the provider that matches your hardware and privacy needs.</p>
                  <div className="provider-grid">
                    {(["auto", "ollama", "mlx", "anthropic", "openai", "minimax", "mock"] as LLMProvider[]).map((p) => (
                      <button
                        key={p}
                        className={`provider-card ${provider === p ? "selected" : ""}`}
                        onClick={() => {
                          setProvider(p);
                          setModel("");
                        }}
                      >
                        <Server size={20} />
                        <div className="provider-name">{p}</div>
                        <div className="provider-meta">
                          {p === "auto" && "Smart routing across all providers"}
                          {p === "ollama" && "Local · Free · macOS/Linux/Windows"}
                          {p === "mlx" && "Local · Apple Silicon · Native"}
                          {p === "anthropic" && "Cloud · Claude Opus/Sonnet/Haiku"}
                          {p === "openai" && "Cloud · GPT-4o/o1/o3-mini"}
                          {p === "minimax" && "Cloud · MiniMax models"}
                          {p === "mock" && "Echo provider for offline demos"}
                        </div>
                        {providers[p] && providers[p].length > 0 && (
                          <div className="provider-models">{providers[p].slice(0, 3).join(", ")}</div>
                        )}
                      </button>
                    ))}
                  </div>
                  {provider !== "auto" && provider !== "mock" && providers[provider]?.length > 0 && (
                    <div className="onboarding-field">
                      <label>Model (optional — uses default if blank)</label>
                      <input
                        list={`models-${provider}`}
                        value={model}
                        onChange={(e) => setModel(e.target.value)}
                        placeholder={providers[provider]?.[0] || ""}
                      />
                      <datalist id={`models-${provider}`}>
                        {(providers[provider] || []).map((m) => <option key={m} value={m} />)}
                      </datalist>
                    </div>
                  )}
                </div>
              )}

              {/* Step 2: Memory */}
              {step === 2 && (
                <div className="onboarding-step">
                  <Brain size={36} color="var(--accent)" />
                  <h2>Tell us about you</h2>
                  <p>This helps the agent give you better answers. You can edit it anytime from the Memory tab.</p>
                  <div className="onboarding-field">
                    <label>Interests</label>
                    <div className="chip-input">
                      {interests.map((i) => (
                        <span key={i} className="chip">
                          {i}
                          <button
                            onClick={() => setInterests((prev) => prev.filter((x) => x !== i))}
                            aria-label={`remove ${i}`}
                          >
                            <X size={10} />
                          </button>
                        </span>
                      ))}
                      <input
                        value={interestDraft}
                        onChange={(e) => setInterestDraft(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && addInterestItem()}
                        placeholder="e.g., rust, compilers, webgpu…"
                      />
                    </div>
                    <div className="chip-suggestions">
                      {["rust", "python", "machine-learning", "compilers", "webgpu", "cryptography", "systems-programming", "databases"]
                        .filter((s) => !interests.includes(s))
                        .slice(0, 4)
                        .map((s) => (
                          <button key={s} onClick={() => setInterests((prev) => Array.from(new Set([...prev, s])))}>
                            + {s}
                          </button>
                        ))}
                    </div>
                  </div>
                  <div className="onboarding-field">
                    <label>What are you working on?</label>
                    <textarea
                      value={workContext}
                      onChange={(e) => setWorkContext(e.target.value)}
                      placeholder="e.g., Building a custom async runtime for embedded Rust"
                      rows={3}
                    />
                  </div>
                </div>
              )}

              {/* Step 3: Test connection */}
              {step === 3 && (
                <div className="onboarding-step">
                  <Zap size={36} color="var(--accent)" />
                  <h2>Test the connection</h2>
                  <p>Send a quick message to make sure everything is wired up.</p>
                  <div className="onboarding-field">
                    <label>Test message</label>
                    <input
                      value={testQuery}
                      onChange={(e) => setTestQuery(e.target.value)}
                    />
                  </div>
                  <button
                    onClick={runTest}
                    disabled={testing}
                    className="onboarding-btn primary"
                  >
                    {testing ? "Testing…" : "Send test message"}
                  </button>
                  {testError && (
                    <div className="onboarding-error">
                      <strong>Test failed:</strong> {testError}
                      <p style={{ marginTop: 8, opacity: 0.7 }}>
                        This is normal if no provider is configured. You can still use the app and configure a provider later.
                      </p>
                    </div>
                  )}
                  {testResult && (
                    <div className="onboarding-success">
                      <div className="test-meta">
                        <span>✓ {testResult.provider} / {testResult.model}</span>
                        <span>{testResult.latency_ms.toFixed(0)}ms</span>
                      </div>
                      <div className="test-content">{testResult.content}</div>
                    </div>
                  )}
                </div>
              )}

              {/* Step 4: Done */}
              {step === 4 && (
                <div className="onboarding-step centered">
                  <motion.div
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    transition={{ type: "spring", stiffness: 200 }}
                  >
                    <Sparkles size={48} color="var(--accent)" />
                  </motion.div>
                  <h2>You're ready</h2>
                  <p>Press <kbd>Cmd+K</kbd> to focus the search, <kbd>Cmd+P</kbd> for privacy, <kbd>Cmd+M</kbd> for memory, <kbd>Cmd+T</kbd> for a new tab.</p>
                  <div className="onboarding-tips">
                    <div className="tip">
                      <MessageSquare size={14} />
                      Try: <em>"What is the latest on WebGPU in 2026?"</em>
                    </div>
                    <div className="tip">
                      <Wrench size={14} />
                      Toggle Agent mode in the header for full plan-execute-verify loops
                    </div>
                  </div>
                </div>
              )}
            </motion.div>
          </AnimatePresence>

          <div className="onboarding-actions">
            <button onClick={back} disabled={step === 0} className="onboarding-btn">
              <ChevronLeft size={14} /> Back
            </button>
            <div style={{ flex: 1 }} />
            {step === 2 && (
              <button onClick={saveMemory} className="onboarding-btn subtle">
                Save &amp; continue
              </button>
            )}
            {step === 4 ? (
              <button onClick={() => close(true)} className="onboarding-btn primary">
                <Sparkles size={14} /> Start researching
              </button>
            ) : (
              <button onClick={next} className="onboarding-btn primary">
                Next <ChevronRight size={14} />
              </button>
            )}
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
};

/** Reset onboarding (for tests / settings). */
export function resetOnboarding() {
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(STEP_KEY);
}
