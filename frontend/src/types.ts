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
  created_at?: string;
}

export interface SessionDetail extends SessionSummary {
  messages: ChatMessage[];
}

export interface RequestUsage {
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
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
  category?: "fast" | "thinking" | "flagship";
}

export interface AppSettings {
  provider: string;
  key_configured: boolean;
  key_env_var: string;
  model: string;
  models: ModelOption[];
  tracing_enabled: boolean;
}

export interface UsageRecord {
  request_id: string;
  model: string;
  total_tokens: number | null;
  latency_ms: number;
  status: string;
  error_code: string | null;
  tool_call_count: number;
  created_at: string;
}

export interface UsageSummary {
  totals: { requests: number; total_tokens: number; avg_latency_ms: number };
  records: UsageRecord[];
}

// ---- Phase 2: documents, workspaces, citations ----------------------------
export interface Workspace {
  id: string;
  name: string;
  created_at: string;
  document_count: number;
  sharing: { allowed: boolean; updated_at: string | null };
}

export interface WorkspaceList {
  workspaces: Workspace[];
  consent_required: boolean;
}

export interface DocumentInfo {
  id: string;
  workspace_id: string;
  filename: string;
  file_type: "pdf" | "md" | "txt";
  size_bytes: number;
  status: string;
  chunk_count: number;
  page_count: number | null;
  index_version: string;
  created_at: string;
  updated_at: string;
}

export interface CitationLocation {
  page?: number;
  heading?: string | null;
  line_start?: number;
  line_end?: number;
}

export interface Citation {
  id: string;
  n: number;
  workspace_id: string;
  document_id: string | null;
  filename: string | null;
  location: CitationLocation | null;
  status: "ok" | "source_deleted";
}

export interface CitationDetail extends Citation {
  request_id: string;
  session_id: string;
  text: string | null;
}

export interface DocumentChatResponse extends ChatResponse {
  citations?: Citation[];
}
