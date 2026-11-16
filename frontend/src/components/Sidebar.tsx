import { useEffect, useRef, useState } from "react";
import type { SessionSummary } from "../types";

interface Props {
  inert?: boolean;
  sessions: SessionSummary[];
  activeId: string | null;
  open: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onClose: () => void;
  onRequestDelete: (session: SessionSummary) => void;
}

export default function Sidebar({ inert, sessions, activeId, open, onSelect, onNew, onClose, onRequestDelete }: Props) {
  const [menuId, setMenuId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close the options menu on outside click or Escape.
  useEffect(() => {
    if (!menuId) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuId(null);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuId(null);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuId]);

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
              <li key={s.id} className={`session-row${s.id === activeId ? " active" : ""}`}>
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
                <div className="dots-wrap" ref={menuId === s.id ? menuRef : undefined}>
                  <button
                    type="button"
                    className="dots"
                    aria-label={`Options for ${s.title}`}
                    aria-haspopup="menu"
                    aria-expanded={menuId === s.id}
                    onClick={() => setMenuId(menuId === s.id ? null : s.id)}
                  >
                    ⋯
                  </button>
                  {menuId === s.id && (
                    <div className="menu-pop" role="menu">
                      <button
                        type="button"
                        role="menuitem"
                        className="danger-text"
                        autoFocus
                        onClick={() => {
                          setMenuId(null);
                          onRequestDelete(s);
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </nav>
    </>
  );
}
