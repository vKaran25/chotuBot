import { useEffect, useRef, useState } from "react";
import type { AppSettings, UsageSummary } from "../types";

interface Props {
  settings: AppSettings | null;
  usage: UsageSummary | null;
  onClose: () => void;
  onSave: (body: { model?: string; monthly_budget_inr?: number }) => Promise<string | null>;
}

const inr = (n: number) => `₹${n.toFixed(n < 1 ? 4 : 2)}`;

export default function SettingsPanel({ settings, usage, onClose, onSave }: Props) {
  const [budget, setBudget] = useState("");
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

  useEffect(() => {
    if (settings) setBudget(String(settings.monthly_budget_inr));
  }, [settings?.monthly_budget_inr]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (body: { model?: string; monthly_budget_inr?: number }) => {
    setMsg((await onSave(body)) ?? "Saved.");
  };
  const budgetNum = Number(budget);
  const budgetValid = budget.trim() !== "" && Number.isFinite(budgetNum) && budgetNum >= 0;

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
              The key is read from <code>{settings.key_env_var}</code> in the backend&apos;s <code>.env</code>. It is never sent to
              this page.
            </p>
            <p className="muted">
              Provider: <code>{settings.provider}</code> · LangSmith tracing: {settings.tracing_enabled ? "on (redacted)" : "off"}
            </p>
          </section>

          <section>
            <h3>Model</h3>
            <label htmlFor="model">Tool-capable model</label>
            <select id="model" value={settings.model} onChange={(e) => void save({ model: e.target.value })}>
              {!settings.models.some((m) => m.id === settings.model) && <option value={settings.model}>{settings.model}</option>}
              {settings.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} ({m.id})
                </option>
              ))}
            </select>
          </section>

          <section>
            <h3>Monthly budget</h3>
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                if (budgetValid) void save({ monthly_budget_inr: budgetNum });
              }}
            >
              <label htmlFor="budget">Budget (₹)</label>
              <input id="budget" type="number" min="0" step="any" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} />
              <button type="submit" disabled={!budgetValid}>
                Save
              </button>
            </form>
            {usage && (
              <>
                <meter min={0} max={usage.budget_inr || 1} value={Math.min(usage.spent_inr, usage.budget_inr || 1)} aria-label="Budget used" />
                <p>
                  {usage.month}: about <strong>{inr(usage.spent_inr)}</strong> of {inr(usage.budget_inr)} used, {inr(usage.remaining_inr)}{" "}
                  left.
                </p>
                <p className="muted">{usage.disclaimer}</p>
              </>
            )}
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
                      <th scope="col">Cost</th>
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
                        <td title={`source: ${r.cost_source}`}>{inr(r.cost_inr)}</td>
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
