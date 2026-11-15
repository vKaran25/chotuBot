import { useId } from "react";

export const MAX_CHARS = 4000;

interface Props {
  value: string;
  pending: boolean;
  onChange: (v: string) => void;
  onSend: () => void;
}

export default function Composer({ value, pending, onChange, onSend }: Props) {
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
      <label htmlFor={id} className="sr-only">
        Message
      </label>
      <textarea
        id={id}
        name="message"
        rows={2}
        maxLength={MAX_CHARS}
        value={value}
        placeholder="Ask about sample project status or notes…"
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
