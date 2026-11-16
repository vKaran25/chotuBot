import type { AppSettings, ChatResponse, SessionDetail, SessionSummary, UsageSummary } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the local backend. Is it running?");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON body handled below */
  }
  if (!res.ok) {
    const e = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `Request failed (${res.status}).`);
  }
  return data as T;
}

export const api = {
  listSessions: () => request<{ sessions: SessionSummary[] }>("/api/sessions").then((r) => r.sessions),
  getSession: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  chat: (message: string, sessionId: string | null) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),
  deleteSession: (id: string) => request<null>(`/api/sessions/${id}`, { method: "DELETE" }),
  getSettings: () => request<AppSettings>("/api/settings"),
  putSettings: (body: { model: string }) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  getUsage: () => request<UsageSummary>("/api/usage"),
};
