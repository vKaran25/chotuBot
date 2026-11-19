import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError, api } from "../api";
import type { DocumentInfo, Workspace } from "../types";

interface Props {
  workspaces: Workspace[];
  consentRequired: boolean;
  workspaceId: string;
  onSelectWorkspace: (id: string) => void;
  onWorkspacesChanged: () => Promise<void> | void;
  onClose: () => void;
}

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const size = (b: number) => (b < 1024 * 1024 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);

export default function DocumentsPanel(p: Props) {
  const { workspaces, consentRequired, workspaceId, onSelectWorkspace, onWorkspacesChanged, onClose } = p;
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const ids = { ws: useId(), file: useId(), name: useId() };
  const ws = workspaces.find((w) => w.id === workspaceId) ?? workspaces[0];

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCloseRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const load = useCallback(async () => {
    try {
      setDocs(await api.listDocuments(workspaceId));
    } catch (e) {
      setError(errText(e));
    }
  }, [workspaceId]);

  useEffect(() => {
    setDocs(null);
    setConfirming(null);
    void load();
  }, [load]);

  const run = async (work: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      setStatus(await work());
      await load();
      await onWorkspacesChanged();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const upload = (file: File | undefined) => {
    if (!file) return;
    void run(async () => {
      const d = await api.uploadDocument(workspaceId, file);
      return `Uploaded ${d.filename}.`;
    }).finally(() => {
      if (fileRef.current) fileRef.current.value = "";
    });
  };

  const create = async () => {
    const name = newName.trim();
    if (!name) return;
    setError(null);
    try {
      const w = await api.createWorkspace(name);
      await onWorkspacesChanged();
      onSelectWorkspace(w.id);
      setCreating(false);
      setNewName("");
      setStatus(`Created workspace ${w.name}.`);
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <aside className="panel" role="dialog" aria-modal="true" aria-label="Documents">
      <div className="panel-head">
        <h2>Documents</h2>
        <button type="button" ref={closeRef} onClick={onClose}>
          Close
        </button>
      </div>
      <div className="panel-body">
        <section>
          <label htmlFor={ids.ws}>Workspace</label>
          <div className="row">
            <select id={ids.ws} value={ws?.id ?? ""} onChange={(e) => onSelectWorkspace(e.target.value)}>
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.document_count})
                </option>
              ))}
            </select>
            <button type="button" onClick={() => setCreating((v) => !v)} aria-expanded={creating}>
              New workspace
            </button>
          </div>
          {creating && (
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                void create();
              }}
            >
              <label htmlFor={ids.name}>Workspace name</label>
              <input id={ids.name} value={newName} maxLength={60} onChange={(e) => setNewName(e.target.value)} />
              <button type="submit" className="primary" disabled={!newName.trim()}>
                Create
              </button>
            </form>
          )}
        </section>

        <section>
          <h3>Sharing with the model</h3>
          {consentRequired ? (
            <>
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!ws?.sharing.allowed}
                  disabled={!ws}
                  onChange={(e) => {
                    const allowed = e.target.checked;
                    if (!ws) return;
                    void run(async () => {
                      await api.setConsent(ws.id, allowed);
                      return allowed ? "Sharing allowed for this workspace." : "Sharing turned off for this workspace.";
                    });
                  }}
                />
                Allow sharing excerpts with the cloud model
              </label>
              <p className="muted">
                When on, the few excerpts that match a question are sent to NVIDIA NIM with that question. Your files stay
                on this computer.
                {ws?.sharing.updated_at && ` Last changed ${new Date(ws.sharing.updated_at).toLocaleString()}.`}
              </p>
            </>
          ) : (
            <p className="muted">The offline demo provider is active: nothing leaves this computer, so no consent is needed.</p>
          )}
        </section>

        <section>
          <label htmlFor={ids.file}>Upload a document (.pdf, .md, .txt, max 10 MB)</label>
          <input
            id={ids.file}
            ref={fileRef}
            type="file"
            accept=".pdf,.md,.txt"
            disabled={busy}
            onChange={(e) => upload(e.target.files?.[0])}
          />
          <p className="muted">Text PDFs only (scanned PDFs have no text to read).</p>
        </section>

        {busy && (
          <p className="muted" role="status">
            Working…
          </p>
        )}
        {status && !busy && (
          <p className="muted" role="status">
            {status}
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        <section>
          <h3>In this workspace</h3>
          {docs === null ? (
            <p className="muted">Loading…</p>
          ) : docs.length === 0 ? (
            <p className="muted">No documents in this workspace yet.</p>
          ) : (
            <ul className="docs" aria-label="Documents">
              {docs.map((d) => (
                <li key={d.id} className="doc">
                  <span className="doc-name">{d.filename}</span>
                  <span className="session-meta">
                    {d.file_type.toUpperCase()} · {size(d.size_bytes)} · {d.chunk_count} {d.chunk_count === 1 ? "chunk" : "chunks"}
                    {d.page_count !== null && ` · ${d.page_count} ${d.page_count === 1 ? "page" : "pages"}`}
                  </span>
                  <span className="doc-actions">
                    {confirming === d.id ? (
                      <>
                        <button
                          type="button"
                          className="danger"
                          disabled={busy}
                          aria-label={`Confirm delete ${d.filename}`}
                          onClick={() => {
                            setConfirming(null);
                            void run(async () => {
                              await api.deleteDocument(workspaceId, d.id);
                              return `Deleted ${d.filename} and its index data.`;
                            });
                          }}
                        >
                          Delete permanently
                        </button>
                        <button type="button" onClick={() => setConfirming(null)}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          aria-label={`Reindex ${d.filename}`}
                          onClick={() =>
                            void run(async () => {
                              await api.reindexDocument(workspaceId, d.id);
                              return `Reindexed ${d.filename}.`;
                            })
                          }
                        >
                          Reindex
                        </button>
                        <button type="button" disabled={busy} aria-label={`Delete ${d.filename}`} onClick={() => setConfirming(d.id)}>
                          Delete
                        </button>
                      </>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  );
}
