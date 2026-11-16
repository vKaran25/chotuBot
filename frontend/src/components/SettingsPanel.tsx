import { useEffect, useRef, useState } from "react";
import type { AppSettings, UsageSummary } from "../types";

interface Props {
  settings: AppSettings | null;
  usage: UsageSummary | null;
  onClose: () => void;
  onSave: (body: { model: string }) => Promise<string | null>;
}

export default function SettingsPanel({ settings, usage, onClose, onSave }: Props) {
  const [msg, setMsg] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Keep the latest onClose without re-running the mount effect (App passes a new function each render).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCloseRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const save = async (body: { model: string }) => {
    setMsg((await onSave(body)) ?? "Saved.");
  };

  return (
    <aside className="panel" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <div className="panel-head">
        <h2 id="settings-title">Settings and usage</h2>
        <button type="button" ref={closeRef} onClick={onClose}>
          Close
        </button>
      </div>
      {!settings ? (
        <p className="muted pad">Loading…</p>
      ) : (
        <div className="panel-body">
          <section>
            <h3>API key ({settings.provider})</h3>
            <p>
              <span className={`badge ${settings.key_configured ? "badge-ok" : "badge-error"}`}>
                {settings.key_configured ? "configured" : "not configured"}
              </span>
            </p>
            <p className="muted">
              Provider: <code>{settings.provider}</code> · LangSmith tracing: {settings.tracing_enabled ? "on (redacted)" : "off"}
            </p>
          </section>

          <section>
            <h3>Model</h3>
            <select id="model" aria-label="Model" value={settings.model} onChange={(e) => void save({ model: e.target.value })}>
              {!settings.models.some((m) => m.id === settings.model) && <option value={settings.model}>{settings.model}</option>}
              {settings.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} ({m.id})
                </option>
              ))}
            </select>
          </section>

          <section>
            <h3>Usage</h3>
            {usage ? (
              <p>
                {usage.totals.requests} requests, {usage.totals.total_tokens.toLocaleString()} tokens, average{" "}
                {usage.totals.avg_latency_ms} ms.
              </p>
            ) : (
              <p className="muted">Loading…</p>
            )}
            <p className="muted">Token counts come from the provider when it reports them.</p>
          </section>

          {msg && (
            <p role="status" className="muted">
              {msg}
            </p>
          )}

          <section>
            <h3>Recent requests</h3>
            {!usage || usage.records.length === 0 ? (
              <p className="muted">No requests recorded yet.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Status</th>
                      <th scope="col">Model</th>
                      <th scope="col">Tokens</th>
                      <th scope="col">Latency</th>
                      <th scope="col">Tools</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.records.map((r) => (
                      <tr key={r.request_id}>
                        <td>{r.status}</td>
                        <td>{r.model}</td>
                        <td>{r.total_tokens ?? "–"}</td>
                        <td>{r.latency_ms} ms</td>
                        <td>{r.tool_call_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}
