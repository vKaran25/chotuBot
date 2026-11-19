import { useId } from "react";

export const MAX_CHARS = 4000;

interface Props {
  value: string;
  pending: boolean;
  onChange: (v: string) => void;
  onSend: () => void;
  /** Phase 2: per-message "Use my documents" switch; hidden when not provided. */
  useDocuments?: boolean;
  onToggleDocuments?: (on: boolean) => void;
  workspaceName?: string;
}

export default function Composer({ value, pending, onChange, onSend, useDocuments, onToggleDocuments, workspaceName }: Props) {
  const id = useId();
  const canSend = value.trim().length > 0 && !pending;
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      {onToggleDocuments && (
        <div className="doc-toggle">
          <label className="check">
            <input type="checkbox" checked={!!useDocuments} onChange={(e) => onToggleDocuments(e.target.checked)} />
            Use my documents
          </label>
          {useDocuments && workspaceName && <span className="muted">Workspace: {workspaceName}</span>}
        </div>
      )}
      <label htmlFor={id} className="sr-only">
        Message
      </label>
      <textarea
        id={id}
        name="message"
        rows={2}
        maxLength={MAX_CHARS}
        value={value}
        placeholder={useDocuments ? "Ask a question about your documents…" : "Ask about sample project status or notes…"}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSend();
          }
        }}
      />
      <button type="submit" className="primary" disabled={!canSend}>
        {pending ? "Sending…" : "Send"}
      </button>
    </form>
  );
}
