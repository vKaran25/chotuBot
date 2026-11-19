import type { Citation } from "../types";

export default function CitationList({ citations }: { citations: Citation[] }) {
  const labels = [...new Set(citations.flatMap((citation) =>
    citation.status === "source_deleted" ? ["Source deleted"] : citation.filename ? [citation.filename] : [],
  ))];

  if (labels.length === 0) return null;
  return (
    <ul className="citations" aria-label="Sources" role="list">
      {labels.map((label) => <li key={label}>{label}</li>)}
    </ul>
  );
}
