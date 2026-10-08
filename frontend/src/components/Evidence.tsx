import { PROVENANCE, type Provenance } from "../lib/palette";

/* ----------------------------------------------------------------------- *
 * Evidence primitives shared by every page.
 *
 * Provenance is the product's core claim, so it gets one consistent mark:
 *   verified   ■ solid ink
 *   semantic   ▨ hatched blue
 *   unverified □ amber outline
 * Pattern + colour + text label, so it survives greyscale and colour-blindness.
 * ----------------------------------------------------------------------- */

export function ProvenanceMark({ source, size = 10 }: { source: string; size?: number }) {
  const p = PROVENANCE[source as Provenance];
  if (!p) return null;
  const style: React.CSSProperties = { display: "inline-block", width: size, height: size, flex: "none" };
  if (source === "llm_verified") return <span aria-hidden style={{ ...style, background: p.color }} />;
  if (source === "semantic") return <span aria-hidden className="hatch-info" style={style} />;
  return <span aria-hidden style={{ ...style, boxShadow: `inset 0 0 0 1.5px ${p.color}` }} />;
}

export function ProvenanceLabel({ source }: { source: string }) {
  const p = PROVENANCE[source as Provenance];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-text-muted" title={p?.description}>
      <ProvenanceMark source={source} />
      {p?.short ?? source}
    </span>
  );
}

/** Persistent key: what each provenance kind means, optionally with counts. */
export function ProvenanceLegend({
  counts,
  compact = false,
}: {
  counts?: Partial<Record<Provenance, number>>;
  compact?: boolean;
}) {
  const keys = Object.keys(PROVENANCE) as Provenance[];
  return (
    <dl className={compact ? "flex flex-wrap gap-x-5 gap-y-1" : "grid gap-3 sm:grid-cols-3"}>
      {keys.map((k) => (
        <div key={k} className="flex gap-2.5">
          <dt className="pt-1">
            <ProvenanceMark source={k} size={12} />
          </dt>
          <dd className="m-0">
            <span className="text-sm font-semibold text-text">
              {PROVENANCE[k].label}
              {counts?.[k] !== undefined && (
                <span className="ml-1.5 font-normal text-text-muted tabular-nums">{counts[k]}</span>
              )}
            </span>
            {!compact && <p className="m-0 mt-0.5 text-xs text-text-muted leading-snug">{PROVENANCE[k].description}</p>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Stacked bar of a technique's mappings by provenance. */
export function ProvenanceBar({
  verified,
  semantic,
  unverified,
  max,
}: {
  verified: number;
  semantic: number;
  unverified: number;
  max: number;
}) {
  const pct = (n: number) => `${(100 * n) / Math.max(1, max)}%`;
  return (
    <div
      className="flex h-2.5 w-full bg-surface-2"
      role="img"
      aria-label={`${verified} verified, ${semantic} semantic, ${unverified} unverified`}
    >
      <span style={{ width: pct(verified) }} className="block bg-text" />
      <span style={{ width: pct(semantic) }} className="block hatch-info" />
      <span className="block" style={{ width: pct(unverified), boxShadow: "inset 0 0 0 1.5px var(--color-warn)" }} />
    </div>
  );
}

/** One error state for every failed query: says what failed, offers retry. */
export function QueryError({
  what,
  error,
  onRetry,
}: {
  what: string;
  error?: unknown;
  onRetry?: () => void;
}) {
  const msg = error instanceof Error ? error.message : "";
  return (
    <div role="alert" className="border border-danger/40 bg-surface-1 px-4 py-3 text-sm">
      <p className="m-0 font-semibold text-danger">Couldn't load {what}.</p>
      <p className="m-0 mt-1 text-text-muted">
        {msg.startsWith("5") || msg.includes("Failed to fetch")
          ? "The API isn't reachable. Check that the backend is running on port 8765."
          : msg || "The request failed."}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 border border-text px-3 py-1 text-sm font-semibold hover:bg-text hover:text-surface-1"
        >
          Retry
        </button>
      )}
    </div>
  );
}
