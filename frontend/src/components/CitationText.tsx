/* Text where each [#NNN] becomes a button that opens that post */

const CITE = /\[#(\d+)\]/g;

export function CitationText({
  text,
  onSelect,
}: {
  text: string;
  onSelect: (postId: number) => void;
}) {
  // Split the text into plain pieces and citation pieces
  const parts: (string | { id: number; raw: string })[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE)) {
    const start = m.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    parts.push({ id: Number(m[1]), raw: m[0] });
    last = start + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));

  return (
    <>
      {/* Plain text stays text; citations become clickable buttons */}
      {parts.map((p, i) =>
        typeof p === "string" ? (
          <span key={i}>{p}</span>
        ) : (
          <button
            key={i}
            onClick={() => onSelect(p.id)}
            className="mx-0.5 inline-flex items-baseline border-b-2 border-accent/60 px-0.5 align-baseline font-mono text-[0.85em] font-semibold leading-none text-accent hover:bg-accent hover:text-surface-1"
            title={`Open cited post #${p.id}`}
          >
            #{p.id}
          </button>
        ),
      )}
    </>
  );
}
