import { useEffect, useRef } from "react";

interface Props {
  title: string;
  message: string;
  confirmLabel: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({ title, message, confirmLabel, busy, error, onConfirm, onCancel }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const cancelFn = useRef(onCancel);
  cancelFn.current = onCancel;

  useEffect(() => {
    cancelRef.current?.focus(); // safest default: Cancel
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && cancelFn.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <div className="scrim show" onClick={onCancel} aria-hidden="true" />
      <div className="confirm" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-msg">
        <h2 id="confirm-title">{title}</h2>
        <p id="confirm-msg">{message}</p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="confirm-actions">
          <button type="button" ref={cancelRef} onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Deleting…" : confirmLabel}
          </button>
        </div>
      </div>
    </>
  );
}
