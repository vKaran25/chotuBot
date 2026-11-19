import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import type { Citation, CitationDetail, CitationLocation } from "../types";

export function formatLocation(loc: CitationLocation | null): string {
  if (!loc) return "";
  if (loc.page !== undefined) return `page ${loc.page}`;
  const parts: string[] = [];
  if (loc.heading) parts.push(loc.heading);
  if (loc.line_start !== undefined) {
    parts.push(loc.line_start === loc.line_end ? `line ${loc.line_start}` : `lines ${loc.line_start}–${loc.line_end}`);
  }
  return parts.join(" · ");
}

const label = (c: Citation) =>
  c.status === "ok" ? `[${c.n}] ${c.filename ?? ""} — ${formatLocation(c.location)}` : `[${c.n}] source deleted`;

function ExcerptDialog({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const [detail, setDetail] = useState<CitationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCloseRef.current();
    window.addEventListener("keydown", onKey);
    let live = true;
    api.getCitation(citation.id).then(
      (d) => live && setDetail(d),
      (e) => live && setError(e instanceof ApiError ? e.message : "Could not load the source."),
    );
    return () => {
      live = false;
      window.removeEventListener("keydown", onKey);
    };
  }, [citation.id]);

  const deleted = detail?.status === "source_deleted";
  return (
    <>
      <div className="scrim show" onClick={onClose} aria-hidden="true" />
      <div className="confirm excerpt" role="dialog" aria-modal="true" aria-label={`Source [${citation.n}]`}>
        <h2>Source [{citation.n}]</h2>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {!detail && !error && <p className="muted">Loading…</p>}
        {deleted && <p className="muted">This source was deleted.</p>}
        {detail && !deleted && (
          <>
            <p>
              <strong>{detail.filename}</strong> <span className="muted">{formatLocation(detail.location)}</span>
            </p>
            {/* Excerpt text is untrusted document content: shown as plain text, never as HTML or Markdown. */}
            <p className="excerpt-text">{detail.text}</p>
          </>
        )}
        <div className="confirm-actions">
          <button type="button" ref={closeRef} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </>
  );
}

export default function CitationList({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<Citation | null>(null);
  if (citations.length === 0) return null;
  return (
    <div className="citations">
      <ol aria-label="Sources">
        {citations.map((c) => (
          <li key={c.id}>
            <button type="button" className={`cite${c.status === "ok" ? "" : " cite-gone"}`} onClick={() => setOpen(c)}>
              {label(c)}
            </button>
          </li>
        ))}
      </ol>
      {open && <ExcerptDialog citation={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
