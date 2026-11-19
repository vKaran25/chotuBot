import { useEffect, useId, useRef, useState } from "react";

export const MAX_CHARS = 4000;

interface Props {
  value: string;
  pending: boolean;
  onChange: (v: string) => void;
  onSend: () => void;
  /** Phase 2: document controls live behind the + button; hidden when not provided. */
  useDocuments?: boolean;
  onToggleDocuments?: (on: boolean) => void;
  workspaceName?: string;
  onAddFile?: (file: File) => void;
  onManageDocuments?: () => void;
}

const PlusIcon = () => (
  <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">
    <path d="M10 4v12M4 10h12" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" fill="none" />
  </svg>
);
const ArrowIcon = () => (
  <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
    <path d="M10 15V5M5.5 9.5 10 5l4.5 4.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" fill="none" />
  </svg>
);
const CheckIcon = () => (
  <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
    <path d="m5 10.5 3.2 3L15 6.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" fill="none" />
  </svg>
);

function DocumentsMenu(props: Required<Pick<Props, "onToggleDocuments" | "onAddFile" | "onManageDocuments">> & {
  useDocuments: boolean;
}) {
  const { useDocuments, onToggleDocuments, onAddFile, onManageDocuments } = props;
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="plus-wrap" ref={wrapRef}>
      <button
        type="button"
        ref={btnRef}
        className="icon-btn"
        aria-label="Documents"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <PlusIcon />
      </button>
      {open && (
        <div className="plus-menu" role="menu" aria-label="Documents">
          <button
            type="button"
            role="menuitem"
            autoFocus
            onClick={() => {
              setOpen(false);
              fileRef.current?.click();
            }}
          >
            Add a file
            <span className="item-note">PDF, Markdown or text, up to 10 MB</span>
          </button>
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={useDocuments}
            onClick={() => {
              setOpen(false);
              onToggleDocuments(!useDocuments);
            }}
          >
            <span className="item-row">
              Use my documents
              <span className="item-check">{useDocuments && <CheckIcon />}</span>
            </span>
            <span className="item-note">Answer from your files, with sources</span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onManageDocuments();
            }}
          >
            Manage documents
          </button>
        </div>
      )}
      <input
        ref={fileRef}
        type="file"
        accept=".pdf,.md,.txt"
        hidden
        aria-label="Add a file"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) onAddFile(f);
        }}
      />
    </div>
  );
}

export default function Composer(p: Props) {
  const { value, pending, onChange, onSend, useDocuments, onToggleDocuments, workspaceName, onAddFile, onManageDocuments } = p;
  const id = useId();
  const canSend = value.trim().length > 0 && !pending;
  const docsAvailable = !!(onToggleDocuments && onAddFile && onManageDocuments);
  return (
    <form
      className={`composer${useDocuments ? " docs-on" : ""}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      <label htmlFor={id} className="sr-only">
        Message
      </label>
      <textarea
        id={id}
        name="message"
        rows={1}
        maxLength={MAX_CHARS}
        value={value}
        placeholder={useDocuments ? "Ask about your documents" : "Message chotuBot"}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSend();
          }
        }}
      />
      <div className="composer-bar">
        {docsAvailable && (
          <DocumentsMenu
            useDocuments={!!useDocuments}
            onToggleDocuments={onToggleDocuments!}
            onAddFile={onAddFile!}
            onManageDocuments={onManageDocuments!}
          />
        )}
        {useDocuments && onToggleDocuments && (
          <span className="doc-chip">
            {workspaceName ? `Documents: ${workspaceName}` : "Documents"}
            <button type="button" className="chip-x" aria-label="Stop using documents" onClick={() => onToggleDocuments(false)}>
              ×
            </button>
          </span>
        )}
        <button type="submit" className="send" disabled={!canSend} aria-label={pending ? "Sending…" : "Send"}>
          <ArrowIcon />
        </button>
      </div>
    </form>
  );
}
