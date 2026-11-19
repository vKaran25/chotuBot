import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import Composer, { AttachmentList, type AttachedFile } from "./components/Composer";
import MessageBody from "./components/MessageBody";
import ConfirmDialog from "./components/ConfirmDialog";
import SettingsPanel from "./components/SettingsPanel";
import Sidebar from "./components/Sidebar";
import ToolActivityList from "./components/ToolActivityList";
import type { AppSettings, ChatMessage, Citation, SessionSummary, UsageSummary, Workspace } from "./types";

interface UiError {
  code: string;
  message: string;
}

const toUiError = (e: unknown): UiError =>
  e instanceof ApiError ? { code: e.code, message: e.message } : { code: "unknown", message: "Something went wrong." };

// Phase 2: files belong to one chat. Each chat with files uses its own workspace (docs/specs/document-chat.md, Web UI).
const chatWorkspaceName = (sessionId: string) => `chat:${sessionId}`;

async function findChatWorkspace(sessionId: string): Promise<Workspace | null> {
  const list = await api.listWorkspaces();
  if (!Array.isArray(list?.workspaces)) return null;
  return list.workspaces.find((w) => w.name === chatWorkspaceName(sessionId)) ?? null;
}

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
  const [streamText, setStreamText] = useState("");
  // Phase 2: documents. Every Phase 2 fetch fails silently so Phase 1 chat is never disturbed by it.
  const [docsAvailable, setDocsAvailable] = useState(false);
  const [chatWs, setChatWs] = useState<string | null>(null);
  const [files, setFiles] = useState<AttachedFile[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [citations, setCitations] = useState<Record<string, Citation[]>>({});
  const loadToken = useRef(0); // ignores responses for a conversation the user already left
  const shownChat = useRef<string | null>(null); // chat whose files are on screen
  const messagesRef = useRef<HTMLDivElement>(null);
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

  const checkDocuments = useCallback(async () => {
    try {
      const list = await api.listWorkspaces();
      setDocsAvailable(Array.isArray(list?.workspaces)); // older backend or unexpected reply: no + button
    } catch {
      /* documents are optional; chat keeps working */
    }
  }, []);

  const loadCitations = async (sessionId: string, token: number) => {
    try {
      const map = await api.sessionCitations(sessionId);
      if (map && typeof map === "object" && token === loadToken.current) setCitations((c) => ({ ...c, ...map }));
    } catch {
      /* citations are optional */
    }
  };

  const loadChatFiles = async (sessionId: string, token: number) => {
    try {
      const ws = await findChatWorkspace(sessionId);
      const docs = ws ? await api.listDocuments(ws.id) : [];
      if (token !== loadToken.current) return;
      setChatWs(ws?.id ?? null);
      setFiles(docs.map((d) => ({ key: d.id, name: d.filename, addedAt: d.created_at })));
    } catch {
      /* files are optional */
    }
  };

  useEffect(() => {
    void refreshSessions();
    void refreshSettingsAndUsage();
    void checkDocuments();
  }, [refreshSessions, refreshSettingsAndUsage, checkDocuments]);

  useEffect(() => {
    const log = messagesRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, pending, streamText, files]);

  const clearChatFiles = () => {
    setChatWs(null);
    setFiles([]);
    setFileError(null);
  };

  const selectSession = async (id: string) => {
    const token = ++loadToken.current;
    setSidebarOpen(false);
    setError(null);
    try {
      const detail = await api.getSession(id);
      if (token !== loadToken.current) return;
      setActiveId(id);
      setMessages(detail.messages);
      shownChat.current = id;
      clearChatFiles();
      void loadCitations(id, token);
      void loadChatFiles(id, token);
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
    shownChat.current = null;
    clearChatFiles();
  };

  const send = async () => {
    const text = input.trim();
    if (!text || pending) return;
    const token = ++loadToken.current;
    const sentIn = activeId;
    // A chat with files answers from them; a chat without files is plain Phase 1 chat.
    const documents = chatWs && files.some((f) => !f.uploading) ? { workspaceId: chatWs } : undefined;
    setError(null);
    setPending(true);
    setStreamText("");
    const sentAt = new Date();
    setMessages((m) => [...m, {
      id: `local-${sentAt.getTime()}`, role: "user", content: text, tool_activity: [], created_at: sentAt.toISOString(),
    }]);
    setInput("");
    let res;
    let streamed = false;
    try {
      res = await api.chat(text, sentIn, documents, (soFar) => {
        streamed = streamed || soFar.length > 0;
        if (token === loadToken.current) setStreamText(soFar);
      });
    } catch (e) {
      setPending(false);
      setStreamText("");
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
    setStreamText("");
    void refreshSettingsAndUsage();
    void refreshSessions();
    const cited = res.citations;
    if (cited?.length) setCitations((c) => ({ ...c, [res.request_id]: cited }));
    if (token !== loadToken.current) return; // user switched conversations meanwhile; it is saved and listed
    if (streamed) {
      // Keep the streamed answer on screen (as the final, cleaned-up text) until the saved chat reloads.
      const final = res;
      setMessages((m) => [...m, { id: `local-a-${Date.now()}`, role: "assistant", content: final.message.content,
                                   tool_activity: final.tool_activity, request_id: final.request_id }]);
    }
    setActiveId(res.session_id);
    shownChat.current = res.session_id;
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

  // + button: add files to this chat (creating the chat first if it is new). Adding a file is the owner's consent
  // to share this chat's matching excerpts with the model (owner decision 2026-11-19).
  const addFiles = async (chosen: File[]) => {
    setFileError(null);
    let sid = activeId;
    let ws = chatWs;
    try {
      if (!sid) {
        sid = (await api.createSession()).id;
        loadToken.current++;
        setActiveId(sid);
        shownChat.current = sid;
        void refreshSessions();
      }
      if (!ws) {
        ws = (await findChatWorkspace(sid))?.id ?? (await api.createWorkspace(chatWorkspaceName(sid))).id;
        await api.setConsent(ws, true);
        if (shownChat.current === sid) setChatWs(ws);
      }
    } catch (e) {
      setFileError(toUiError(e).message);
      return;
    }
    for (const [i, file] of chosen.entries()) {
      const key = `adding-${Date.now()}-${i}`;
      const here = () => shownChat.current === sid;
      if (here()) setFiles((f) => [...f, { key, name: file.name, uploading: true }]);
      try {
        const d = await api.uploadDocument(ws, file);
        if (here()) setFiles((f) => f.map((x) => (x.key === key ? { key: d.id, name: d.filename, addedAt: d.created_at } : x)));
      } catch (e) {
        if (here()) {
          setFiles((f) => f.filter((x) => x.key !== key));
          setFileError(`${file.name}: ${toUiError(e).message}`);
        }
      }
    }
  };

  const removeFile = async (key: string) => {
    if (!chatWs) return;
    setFileError(null);
    try {
      await api.deleteDocument(chatWs, key);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        setFileError(toUiError(e).message);
        return;
      }
    }
    setFiles((f) => f.filter((x) => x.key !== key));
  };

  // Deleting a chat also deletes the files added to it.
  const deleteChatFiles = async (sessionId: string) => {
    try {
      const ws = sessionId === activeId && chatWs ? chatWs : (await findChatWorkspace(sessionId))?.id;
      if (!ws) return;
      const docs = await api.listDocuments(ws);
      await Promise.all(docs.map((d) => api.deleteDocument(ws, d.id).catch(() => undefined)));
    } catch {
      /* best effort */
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
    if (docsAvailable) await deleteChatFiles(target.id);
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

  const overlay = settingsOpen || pendingDelete !== null;
  const draftFiles: AttachedFile[] = [];
  const messageFiles = new Map<ChatMessage["id"], AttachedFile[]>();
  // A file belongs in the transcript with the first message sent after it was added.
  for (const file of files) {
    const addedAt = Date.parse(file.addedAt ?? "");
    const message = messages.find((m) => m.role === "user" && Date.parse(m.created_at ?? "") >= addedAt);
    if (!message) {
      draftFiles.push(file);
    } else {
      const attachments = messageFiles.get(message.id);
      if (attachments) attachments.push(file);
      else messageFiles.set(message.id, [file]);
    }
  }

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

        <div className="messages" ref={messagesRef} role="log" aria-live="polite" aria-label="Conversation">
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
                <AttachmentList files={messageFiles.get(m.id)} onRemoveFile={(key) => void removeFile(key)} />
                {m.role === "assistant" ? (
                  <MessageBody text={m.content} citations={m.request_id ? citations[m.request_id] : undefined} />
                ) : (
                  <p className="msg-text">{m.content}</p>
                )}
                <ToolActivityList activity={m.tool_activity} />
              </article>
            ))}
            {pending &&
              (streamText ? (
                <article className="msg assistant streaming" aria-busy="true">
                  <h2 className="sr-only">Assistant</h2>
                  <MessageBody text={streamText} />
                </article>
              ) : (
                <p className="muted thinking" role="status">
                  Thinking…
                </p>
              ))}
          </div>
        </div>

        <div className="dock">
          {fileError && (
            <p className="error" role="alert">
              {fileError}
            </p>
          )}
          {error && (
            <p className="error" role="alert">
              {error.message} <span className="muted">({error.code})</span>
            </p>
          )}

          <Composer
            value={input}
            pending={pending}
            onChange={setInput}
            onSend={() => void send()}
            files={draftFiles}
            onAddFiles={docsAvailable ? (f) => void addFiles(f) : undefined}
            onRemoveFile={(key) => void removeFile(key)}
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

      {settingsOpen && (
        <>
          <div className="scrim show" onClick={closeSettings} aria-hidden="true" />
          <SettingsPanel settings={settings} usage={usage} onClose={closeSettings} onSave={saveSettings} />
        </>
      )}
    </div>
  );
}
