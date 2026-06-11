// Agent API helpers — wraps the /v2/agent/* endpoints and parses SSE streams
import { localFetch } from "./api";
import type { AgentEvent, Plan, ToolSpec } from "./types";

const BACKEND_URL = import.meta.env.DEV ? "" : "http://localhost:8001";

export async function listAgentTools(): Promise<{ tools: ToolSpec[]; stats: any[] }> {
  const r = await localFetch("/v2/agent/tools");
  return r.json();
}

export async function listLLMProviders(): Promise<{
  default_provider: string;
  fallback_chain: string[];
  providers: string[];
  models: Record<string, string[]>;
}> {
  const r = await localFetch("/v2/llm/providers");
  return r.json();
}

/**
 * Run the agent loop and stream events via Server-Sent Events.
 * Yields parsed AgentEvent objects.
 */
export async function* runAgentStream(opts: {
  query: string;
  user_id?: string;
  max_steps?: number;
  max_tokens?: number;
  max_seconds?: number;
}): AsyncGenerator<AgentEvent> {
  const url = `${BACKEND_URL}/v2/agent/run`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: opts.query,
      user_id: opts.user_id || "default",
      max_steps: opts.max_steps ?? 10,
      max_tokens: opts.max_tokens ?? 30000,
      max_seconds: opts.max_seconds ?? 120,
      stream: true,
    }),
  });
  if (!resp.ok || !resp.body) {
    yield {
      type: "run_failed",
      run_id: "",
      timestamp: Date.now() / 1000,
      data: { error: `HTTP ${resp.status}` },
    };
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let dataLine = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) currentEvent = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLine += line.slice(6);
      }
      if (dataLine) {
        try {
          const ev = JSON.parse(dataLine);
          ev.type = ev.type || currentEvent;
          yield ev as AgentEvent;
        } catch (e) {
          // ignore parse errors
        }
      }
    }
  }
}

/**
 * Convenience: run a research query via the agent and collect events.
 * Calls `onEvent` for each event, returns the final answer + plan.
 */
export async function runResearchWithAgent(
  query: string,
  onEvent: (ev: AgentEvent) => void,
  opts: { user_id?: string; max_steps?: number } = {},
): Promise<{ answer: string; plan: Plan | null; sources: string[]; usage: any }> {
  let answer = "";
  let plan: Plan | null = null;
  const sources: string[] = [];
  let usage: any = {};
  for await (const ev of runAgentStream({
    query,
    user_id: opts.user_id || "default",
    max_steps: opts.max_steps ?? 10,
  })) {
    onEvent(ev);
    if (ev.type === "plan_created") plan = ev.data.plan as Plan;
    if (ev.type === "answer_ready") {
      answer = ev.data.answer;
      if (Array.isArray(ev.data.sources)) sources.push(...ev.data.sources);
      if (ev.data.usage) usage = ev.data.usage;
    }
  }
  return { answer, plan, sources, usage };
}
