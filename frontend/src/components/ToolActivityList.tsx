import type { ToolActivity } from "../types";

const LABEL: Record<ToolActivity["status"], string> = { ok: "succeeded", error: "failed", denied: "blocked" };

export default function ToolActivityList({ activity }: { activity: ToolActivity[] }) {
  if (activity.length === 0) return null;
  return (
    <ul className="tools" aria-label="Tool activity">
      {activity.map((t) => (
        <li key={t.id}>
          <details>
            <summary>
              <code>{t.name}</code>
              <span className={`badge badge-${t.status}`}>{LABEL[t.status]}</span>
            </summary>
            <dl>
              <dt>Arguments</dt>
              <dd>
                <pre>{JSON.stringify(t.args, null, 2)}</pre>
              </dd>
              {t.status === "ok" ? (
                <>
                  <dt>Result</dt>
                  <dd>
                    <pre>{JSON.stringify(t.result, null, 2)}</pre>
                  </dd>
                </>
              ) : (
                <>
                  <dt>Error</dt>
                  <dd>
                    <pre>{t.error}</pre>
                  </dd>
                </>
              )}
            </dl>
          </details>
        </li>
      ))}
    </ul>
  );
}
