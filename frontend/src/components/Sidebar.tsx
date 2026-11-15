import type { SessionSummary } from "../types";

interface Props {
  inert?: boolean;
  sessions: SessionSummary[];
  activeId: string | null;
  open: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onClose: () => void;
}

export default function Sidebar({ inert, sessions, activeId, open, onSelect, onNew, onClose }: Props) {
  return (
    <>
      {open && <div className="scrim" onClick={onClose} aria-hidden="true" />}
      <nav className={`sidebar${open ? " open" : ""}`} aria-label="Conversations" inert={inert}>
        <div className="sidebar-head">
          <h1>chotuBot</h1>
          <button type="button" className="primary" onClick={onNew}>
            New chat
          </button>
        </div>
        {sessions.length === 0 ? (
          <p className="muted pad">No conversations yet.</p>
        ) : (
          <ul className="sessions">
            {sessions.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  className={s.id === activeId ? "session active" : "session"}
                  aria-current={s.id === activeId ? "true" : undefined}
                  onClick={() => onSelect(s.id)}
                >
                  <span className="session-title">{s.title}</span>
                  <span className="session-meta">
                    {s.message_count} messages · {new Date(s.updated_at).toLocaleDateString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>
    </>
  );
}
