import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import Composer from "./components/Composer";
import MessageBody from "./components/MessageBody";
import ConfirmDialog from "./components/ConfirmDialog";
import SettingsPanel from "./components/SettingsPanel";
import Sidebar from "./components/Sidebar";
import ToolActivityList from "./components/ToolActivityList";
import CitationList from "./components/CitationList";
import DocumentsPanel from "./components/DocumentsPanel";
import type { AppSettings, ChatMessage, Citation, SessionSummary, UsageSummary, WorkspaceList } from "./types";

const WS_KEY = "chotu.workspace";
const readWs = () => {
  try {
    return localStorage.getItem(WS_KEY) ?? "default";
  } catch {
    return "default";
  }
};

interface UiError {
  code: string;
  message: string;
}

const toUiError = (e: unknown): UiError =>
  e instanceof ApiError ? { code: e.code, message: e.message } : { code: "unknown", message: "Something went wrong." };

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<UiError | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // Phase 2: documents. Every Phase 2 fetch fails silently so Phase 1 chat is never disturbed by it.
  const [workspaces, setWorkspaces] = useState<WorkspaceList | null>(null);
  const [workspaceId, setWorkspaceIdState] = useState<string>(readWs);
  const [useDocs, setUseDocs] = useState(false);
  const [docsOpen, setDocsOpen] = useState(false);
  const [citations, setCitations] = useState<Record<string, Citation[]>>({});
  const [docNotice, setDocNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const loadToken = useRef(0); // ignores responses for a conversation the user already left
  const endRef = useRef<HTMLDivElement>(null);
  const settingsBtnRef = useRef<HTMLButtonElement>(null);
  const restoreSettingsFocus = useRef(false);

  const closeSettings = useCallback(() => {
    restoreSettingsFocus.current = true;
    setSettingsOpen(false);
  }, []);

  useLayoutEffect(() => {
    if (!settingsOpen && restoreSettingsFocus.current) {
      restoreSettingsFocus.current = false;
      settingsBtnRef.current?.focus();
    }
  }, [settingsOpen]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSidebarOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions());
    } catch (e) {
      setError(toUiError(e));
    }
  }, []);

  const refreshSettingsAndUsage = useCallback(async () => {
    const [s, u] = await Promise.allSettled([api.getSettings(), api.getUsage()]);
    if (s.status === "fulfilled") setSettings(s.value);
    if (u.status === "fulfilled") setUsage(u.value);
  }, []);

  const refreshWorkspaces = useCallback(async () => {
    try {
      const list = await api.listWorkspaces();
      if (!Array.isArray(list?.workspaces)) return; // older backend or unexpected reply: hide document features
      setWorkspaces(list);
      setWorkspaceIdState((cur) => (list.workspaces.some((w) => w.id === cur) ? cur : "default"));
    } catch {
      /* documents are optional; chat keeps working */
    }
  }, []);

  const setWorkspaceId = (id: string) => {
    setWorkspaceIdState(id);
    try {
      localStorage.setItem(WS_KEY, id);
    } catch {
      /* ignore */
    }
  };

  const loadCitations = async (sessionId: string, token: number) => {
    try {
      const map = await api.sessionCitations(sessionId);
      if (map && typeof map === "object" && token === loadToken.current) setCitations((c) => ({ ...c, ...map }));
    } catch {
      /* citations are optional */
    }
  };

  useEffect(() => {
    void refreshSessions();
    void refreshSettingsAndUsage();
    void refreshWorkspaces();
  }, [refreshSessions, refreshSettingsAndUsage, refreshWorkspaces]);

  const activeWorkspace = workspaces?.workspaces.find((w) => w.id === workspaceId);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  const selectSession = async (id: string) => {
    const token = ++loadToken.current;
    setSidebarOpen(false);
    setError(null);
    try {
      const detail = await api.getSession(id);
      if (token !== loadToken.current) return;
      setActiveId(id);
      setMessages(detail.messages);
      void loadCitations(id, token);
    } catch (e) {
      if (token === loadToken.current) setError(toUiError(e));
    }
  };

  const newChat = () => {
    loadToken.current++;
    setActiveId(null);
    setMessages([]);
    setError(null);
    setSidebarOpen(false);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || pending) return;
    const token = ++loadToken.current;
    const sentIn = activeId;
    setError(null);
    setPending(true);
    setMessages((m) => [...m, { id: `local-${Date.now()}`, role: "user", content: text, tool_activity: [] }]);
    setInput("");
    let res;
    try {
      res = useDocs ? await api.chat(text, sentIn, { workspaceId }) : await api.chat(text, sentIn);
    } catch (e) {
      setPending(false);
      void refreshSettingsAndUsage();
      if (token !== loadToken.current) return;
      // Failed requests are not saved server-side, so roll back the optimistic message and restore the
      // draft (only if the owner has not typed something new meanwhile).
      setMessages((m) => m.slice(0, -1));
      setInput((cur) => (cur === "" ? text : cur));
      setError(toUiError(e));
      return;
    }
    // The exchange is saved now. A failure below must never roll it back or invite a duplicate resend.
    setPending(false);
    void refreshSettingsAndUsage();
    void refreshSessions();
    const cited = res.citations;
    if (cited?.length) setCitations((c) => ({ ...c, [res.request_id]: cited }));
    if (token !== loadToken.current) return; // user switched conversations meanwhile; it is saved and listed
    setActiveId(res.session_id);
    try {
      const detail = await api.getSession(res.session_id);
      if (token === loadToken.current) setMessages(detail.messages);
    } catch (e) {
      if (token === loadToken.current) {
        const u = toUiError(e);
        setError({ code: u.code, message: `Your message was sent and saved, but reloading it failed: ${u.message} Reopen it from the sidebar.` });
      }
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete || deleting) return;
    const target = pendingDelete;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteSession(target.id);
    } catch (e) {
      // 404 means it is already gone, which is the outcome the owner wanted.
      if (!(e instanceof ApiError && e.status === 404)) {
        setDeleteError(toUiError(e).message);
        setDeleting(false);
        return;
      }
    }
    if (target.id === activeId) newChat();
    setSessions((list) => list.filter((s) => s.id !== target.id));
    setPendingDelete(null);
    setDeleting(false);
    void refreshSessions();
    void refreshSettingsAndUsage();
  };

  const cancelDelete = () => {
    if (deleting) return;
    setPendingDelete(null);
    setDeleteError(null);
  };

  const saveSettings = async (body: { model: string }) => {
    try {
      setSettings(await api.putSettings(body));
      setUsage(await api.getUsage());
      return null;
    } catch (e) {
      return toUiError(e).message;
    }
  };

  const allowSharing = async () => {
    try {
      await api.setConsent(workspaceId, true);
      setError(null);
      void refreshWorkspaces();
    } catch (e) {
      setError(toUiError(e));
    }
  };

  // Same upload API as the Documents panel; adds the file to the selected workspace.
  const addFile = async (file: File) => {
    setDocNotice({ kind: "ok", text: `Adding ${file.name}…` });
    try {
      const d = await api.uploadDocument(workspaceId, file);
      setDocNotice({ kind: "ok", text: `Added ${d.filename} to ${activeWorkspace?.name ?? "your documents"}.` });
      void refreshWorkspaces();
    } catch (e) {
      setDocNotice({ kind: "error", text: toUiError(e).message });
    }
  };

  const openDocuments = () => {
    setDocNotice(null);
    setDocsOpen(true);
    void refreshWorkspaces();
  };

  const overlay = settingsOpen || pendingDelete !== null || docsOpen;

  return (
    <div className="app">
      <Sidebar
        inert={overlay}
        sessions={sessions}
        activeId={activeId}
        open={sidebarOpen}
        onSelect={(id) => void selectSession(id)}
        onNew={newChat}
        onClose={() => setSidebarOpen(false)}
        onRequestDelete={(s) => {
          setDeleteError(null);
          setPendingDelete(s);
        }}
      />
      <main className="chat" inert={overlay}>
        <header className="chat-head">
          <button type="button" className="menu" onClick={() => setSidebarOpen(true)} aria-label="Open conversations">
            ☰
          </button>
          <span className="chat-title">{sessions.find((s) => s.id === activeId)?.title ?? "New conversation"}</span>
          <button
            type="button"
            className="ghost"
            ref={settingsBtnRef}
            onClick={() => {
              setSettingsOpen(true);
              void refreshSettingsAndUsage();
            }}
          >
            Settings
          </button>
        </header>

        {settings && !settings.key_configured && settings.provider !== "demo" && (
          <p className="notice" role="status">
            No API key is configured on the backend. Add <code>{settings.key_env_var}</code> to <code>.env</code> and restart.
          </p>
        )}

        <div className="messages" role="log" aria-live="polite" aria-label="Conversation">
          <div className="thread">
          {messages.length === 0 && !pending && (
            <div className="empty">
              <p className="empty-title">How can I help?</p>
              <p className="muted">Try “What is the status of atlas?” or “Search my notes for budget”.</p>
            </div>
          )}
          {messages.map((m) => (
            <article key={m.id} className={`msg ${m.role}`}>
              <h2 className="sr-only">{m.role === "user" ? "You" : "Assistant"}</h2>
              {m.role === "assistant" ? <MessageBody text={m.content} /> : <p className="msg-text">{m.content}</p>}
              {m.role === "assistant" && m.request_id && Array.isArray(citations[m.request_id]) && (
                <CitationList citations={citations[m.request_id]} />
              )}
              <ToolActivityList activity={m.tool_activity} />
            </article>
          ))}
          {pending && (
            <p className="muted thinking" role="status">
              Thinking…
            </p>
          )}
          <div ref={endRef} />
          </div>
        </div>

        <div className="dock">
        {docNotice && (
          <p className={docNotice.kind === "error" ? "error" : "doc-notice"} role={docNotice.kind === "error" ? "alert" : "status"}>
            {docNotice.text}
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error.message} <span className="muted">({error.code})</span>
            {error.code === "document_consent_required" && (
              <>
                {" "}
                <button type="button" onClick={() => void allowSharing()}>
                  Allow sharing for this workspace
                </button>
              </>
            )}
          </p>
        )}

        <Composer
          value={input}
          pending={pending}
          onChange={setInput}
          onSend={() => void send()}
          useDocuments={useDocs}
          onToggleDocuments={workspaces ? setUseDocs : undefined}
          workspaceName={activeWorkspace?.name}
          onAddFile={workspaces ? (f) => void addFile(f) : undefined}
          onManageDocuments={workspaces ? openDocuments : undefined}
        />
        </div>
      </main>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this chat?"
          message={`Are you sure you want to delete “${pendingDelete.title}”? It will be permanently removed from your history and from the assistant's memory of this conversation. This can't be undone.`}
          confirmLabel="Delete"
          busy={deleting}
          error={deleteError}
          onConfirm={() => void confirmDelete()}
          onCancel={cancelDelete}
        />
      )}

      {docsOpen && (
        <>
          <div className="scrim show" onClick={() => setDocsOpen(false)} aria-hidden="true" />
          <DocumentsPanel
            workspaces={workspaces?.workspaces ?? []}
            consentRequired={workspaces?.consent_required ?? true}
            workspaceId={workspaceId}
            onSelectWorkspace={setWorkspaceId}
            onWorkspacesChanged={refreshWorkspaces}
            onClose={() => setDocsOpen(false)}
          />
        </>
      )}

      {settingsOpen && (
        <>
          <div className="scrim show" onClick={closeSettings} aria-hidden="true" />
          <SettingsPanel settings={settings} usage={usage} onClose={closeSettings} onSave={saveSettings} />
        </>
      )}
    </div>
  );
}
