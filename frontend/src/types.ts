export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ToolActivity {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "ok" | "error" | "denied";
  result: unknown;
  error: string | null;
}

export interface ChatMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  tool_activity: ToolActivity[];
  request_id?: string | null;
}

export interface SessionDetail extends SessionSummary {
  messages: ChatMessage[];
}

export interface RequestUsage {
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number;
  cost_inr: number;
  cost_source: string;
  latency_ms: number;
  tool_call_count: number;
}

export interface ChatResponse {
  session_id: string;
  request_id: string;
  message: { role: "assistant"; content: string };
  tool_activity: ToolActivity[];
  usage: RequestUsage;
}

export interface ModelOption {
  id: string;
  label: string;
}

export interface AppSettings {
  provider: string;
  key_configured: boolean;
  key_env_var: string;
  model: string;
  models: ModelOption[];
  monthly_budget_inr: number;
  usd_to_inr: number;
  tracing_enabled: boolean;
}

export interface UsageRecord {
  request_id: string;
  model: string;
  total_tokens: number | null;
  cost_inr: number;
  cost_source: string;
  latency_ms: number;
  status: string;
  error_code: string | null;
  tool_call_count: number;
  created_at: string;
}

export interface UsageSummary {
  month: string;
  spent_inr: number;
  budget_inr: number;
  remaining_inr: number;
  usd_to_inr: number;
  disclaimer: string;
  records: UsageRecord[];
}
