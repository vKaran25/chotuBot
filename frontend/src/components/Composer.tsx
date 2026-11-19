import { useId, useRef } from "react";

export const MAX_CHARS = 4000;

export interface AttachedFile {
  key: string;
  name: string;
  addedAt?: string;
  uploading?: boolean;
}

interface Props {
  value: string;
  pending: boolean;
  onChange: (v: string) => void;
  onSend: () => void;
  /** Phase 2: files attached to this chat. The + button is hidden when `onAddFiles` is not provided. */
  files?: AttachedFile[];
  onAddFiles?: (files: File[]) => void;
  onRemoveFile?: (key: string) => void;
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
const FileIcon = () => (
  <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
    <path d="M6 2.75h5.2L15 6.6v10.65H6z M11 2.75V7h4" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" fill="none" />
  </svg>
);

export function AttachmentList({ files, onRemoveFile }: Pick<Props, "files" | "onRemoveFile">) {
  if (!files?.length) return null;
  return (
    <ul className="attachments" aria-label="Attached files">
      {files.map((f) => (
        <li key={f.key} className={`attachment${f.uploading ? " is-uploading" : ""}`}>
          <FileIcon />
          <span className="attachment-name" title={f.name}>
            {f.name}
          </span>
          {f.uploading ? (
            <span className="attachment-state">Adding…</span>
          ) : (
            onRemoveFile && (
              <button type="button" className="chip-x" aria-label={`Remove ${f.name}`} onClick={() => onRemoveFile(f.key)}>
                ×
              </button>
            )
          )}
        </li>
      ))}
    </ul>
  );
}

export default function Composer({ value, pending, onChange, onSend, files = [], onAddFiles, onRemoveFile }: Props) {
  const id = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const uploading = files.some((f) => f.uploading);
  const canSend = value.trim().length > 0 && !pending && !uploading;
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      <AttachmentList files={files} onRemoveFile={onRemoveFile} />
      <label htmlFor={id} className="sr-only">
        Message
      </label>
      <textarea
        id={id}
        name="message"
        rows={1}
        maxLength={MAX_CHARS}
        value={value}
        placeholder={files.length > 0 ? "Ask about your files" : "Message chotuBot"}
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
        {onAddFiles && (
          <>
            <button
              type="button"
              className="icon-btn"
              aria-label="Add files"
              title="Add files (PDF, Markdown or text, up to 10 MB)"
              onClick={() => fileRef.current?.click()}
            >
              <PlusIcon />
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.md,.txt"
              multiple
              hidden
              aria-label="Choose files"
              onChange={(e) => {
                const chosen = Array.from(e.target.files ?? []);
                e.target.value = "";
                if (chosen.length) onAddFiles(chosen);
              }}
            />
          </>
        )}
        <button type="submit" className="send" disabled={!canSend} aria-label={pending ? "Sending…" : "Send"}>
          <ArrowIcon />
        </button>
      </div>
    </form>
  );
}
