/* Renders text containing [#NNN] post citations, where each citation becomes
 * a clickable button that surfaces the post via the shared DetailPanel. */

const CITE = /\[#(\d+)\]/g;

export function CitationText({
  text,
  onSelect,
}: {
  text: string;
  onSelect: (postId: number) => void;
}) {
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
      {parts.map((p, i) =>
        typeof p === "string" ? (
          <span key={i}>{p}</span>
        ) : (
          <button
            key={i}
            onClick={() => onSelect(p.id)}
            className="inline-flex items-baseline mx-0.5 px-1 py-px font-mono text-[11px] text-accent border border-accent/40 hover:bg-accent/15 hover:border-accent transition-colors leading-none align-baseline"
            title={`open post #${p.id}`}
          >
            #{p.id}
          </button>
        ),
      )}
    </>
  );
}
