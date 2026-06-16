// Types for the unified LLM / agent / memory layer

export type LLMProvider =
  | "auto"
  | "ollama"
  | "mlx"
  | "anthropic"
  | "openai"
  | "minimax"
  | "mock";

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  name?: string;
  tool_call_id?: string;
  tool_calls?: any[];
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface ChatResponse {
  content: string;
  model: string;
  provider: string;
  usage: Usage;
  finish_reason: string;
  latency_ms: number;
}

// ---- Agent event types ----

export type AgentEventType =
  | "run_started"
  | "plan_created"
  | "step_started"
  | "tool_called"
  | "tool_failed"
  | "step_verified"
  | "replanned"
  | "answer_ready"
  | "run_completed"
  | "run_failed"
  | "log";

export interface AgentEvent {
  type: AgentEventType;
  run_id: string;
  timestamp: number;
  data: any;
}

export interface PlanStep {
  index: number;
  description: string;
  tool: string | null;
  args: Record<string, any>;
  status: "pending" | "running" | "succeeded" | "failed" | "skipped";
  result?: any;
  error?: string;
  verification?: { advanced: boolean; confidence: number; feedback: string };
}

export interface Plan {
  steps: PlanStep[];
  raw?: string;
}

// ---- Memory types ----

export interface UserProfile {
  user_id: string;
  display_name: string;
  interests: string[];
  expertise: Record<string, string>;
  language: string;
  work_context: string;
  preferences: Record<string, any>;
  created_at: number;
  updated_at: number;
}

export interface SessionMemory {
  session_id: string;
  user_id: string;
  topic: string;
  summary: string;
  active_goals: string[];
  entities: string[];
  created_at: number;
  last_active: number;
}

export interface MemoryEntry {
  id: number;
  content: string;
  category: string;
  importance: number;
  score?: number;
  matched_by?: string;
  created_at: number;
}

export interface MemoryHit {
  id: number;
  content: string;
  category: string;
  importance: number;
  score: number;
  matched_by: string;
  created_at: number;
}

export interface ToolSpec {
  name: string;
  description: string;
  parameters: any;
  requires_network: boolean;
  risk_level: "low" | "medium" | "high";
}
