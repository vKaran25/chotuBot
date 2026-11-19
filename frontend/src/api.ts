import type {
  AppSettings,
  Citation,
  CitationDetail,
  DocumentChatResponse,
  DocumentInfo,
  SessionDetail,
  SessionSummary,
  UsageSummary,
  Workspace,
  WorkspaceList,
} from "./types";

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
      // JSON bodies are strings; a FormData upload lets the browser set its multipart boundary itself.
      headers: typeof init?.body === "string" ? { "Content-Type": "application/json" } : undefined,
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
  // With documents off the body is exactly the Phase 1 body.
  chat: (message: string, sessionId: string | null, documents?: { workspaceId: string }) =>
    request<DocumentChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify(
        documents
          ? { message, session_id: sessionId, use_documents: true, workspace_id: documents.workspaceId }
          : { message, session_id: sessionId },
      ),
    }),
  deleteSession: (id: string) => request<null>(`/api/sessions/${id}`, { method: "DELETE" }),
  getSettings: () => request<AppSettings>("/api/settings"),
  putSettings: (body: { model: string }) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  getUsage: () => request<UsageSummary>("/api/usage"),

  // ---- Phase 2 ----
  listWorkspaces: () => request<WorkspaceList>("/api/workspaces"),
  createWorkspace: (name: string) =>
    request<Workspace>("/api/workspaces", { method: "POST", body: JSON.stringify({ name }) }),
  setConsent: (workspaceId: string, allowed: boolean) =>
    request<Workspace>(`/api/workspaces/${encodeURIComponent(workspaceId)}/consent`, {
      method: "PUT",
      body: JSON.stringify({ allowed }),
    }),
  listDocuments: (workspaceId: string) =>
    request<{ documents: DocumentInfo[] }>(`/api/documents?workspace_id=${encodeURIComponent(workspaceId)}`).then(
      (r) => r.documents,
    ),
  uploadDocument: (workspaceId: string, file: File) => {
    const form = new FormData();
    form.append("workspace_id", workspaceId);
    form.append("file", file);
    return request<DocumentInfo>("/api/documents", { method: "POST", body: form });
  },
  reindexDocument: (workspaceId: string, id: string) =>
    request<DocumentInfo>(`/api/documents/${id}/reindex?workspace_id=${encodeURIComponent(workspaceId)}`, {
      method: "POST",
    }),
  deleteDocument: (workspaceId: string, id: string) =>
    request<null>(`/api/documents/${id}?workspace_id=${encodeURIComponent(workspaceId)}`, { method: "DELETE" }),
  getCitation: (id: string) => request<CitationDetail>(`/api/citations/${id}`),
  sessionCitations: (sessionId: string) =>
    request<{ citations: Record<string, Citation[]> }>(`/api/sessions/${sessionId}/citations`).then((r) => r.citations),
};
