import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type TechniqueListItem } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { ProvenanceLabel, ProvenanceLegend, QueryError } from "../components/Evidence";

/* ----------------------------------------------------------------------- *
 * MITRE ATT&CK Enterprise heatmap.
 *
 * Layout: classic Navigator-style matrix — tactics across the top, the
 * techniques observed in our corpus stacked underneath each tactic column.
 * Cells shade by post_count (darker = more observed). Click a cell → fetch
 * the technique's posts and surface them via the shared DetailPanel.
 * ----------------------------------------------------------------------- */

// Canonical Enterprise ATT&CK tactics, grouped into 3 kill-chain phases so
// the matrix breathes across rows instead of cramming 14 columns into one.
type Tactic = { slug: string; label: string };
const TACTIC_ROWS: { phase: string; tactics: Tactic[] }[] = [
  {
    phase: "Pre-compromise → Foothold",
    tactics: [
      { slug: "reconnaissance", label: "Recon" },
      { slug: "resource-development", label: "Resource Dev" },
      { slug: "initial-access", label: "Initial Access" },
      { slug: "execution", label: "Execution" },
      { slug: "persistence", label: "Persistence" },
      { slug: "privilege-escalation", label: "Priv Esc" },
      // ATT&CK v18 split the old "Defense Evasion" tactic into Stealth and
      // Defense Impairment; the corpus we ingest uses the new slugs.
      { slug: "stealth", label: "Stealth" },
      { slug: "defense-impairment", label: "Defense Impairment" },
    ],
  },
  {
    phase: "Operate → Objective",
    tactics: [
      { slug: "credential-access", label: "Cred Access" },
      { slug: "discovery", label: "Discovery" },
      { slug: "lateral-movement", label: "Lateral Move" },
      { slug: "collection", label: "Collection" },
      { slug: "command-and-control", label: "C2" },
      { slug: "exfiltration", label: "Exfiltration" },
      { slug: "impact", label: "Impact" },
    ],
  },
];
const TACTICS: Tactic[] = TACTIC_ROWS.flatMap((r) => r.tactics);

const UNCATEGORISED = "__none__";

// Cells past the midpoint of the ramp carry white text.
function onInk(count: number, max: number): boolean {
  return count > 0 && Math.log(1 + count) / Math.log(1 + max) > 0.5;
}

// Ink ramp on a log scale: a few heavily-mapped techniques shouldn't wash
// out the long tail.
function shade(count: number, max: number): string {
  if (count <= 0) return "rgba(230,237,231,0.04)";
  const t = Math.min(1, Math.log(1 + count) / Math.log(1 + max));
  const alpha = 0.06 + t * 0.88;
  return `rgba(230, 237, 231, ${alpha.toFixed(3)})`;
}

export function Heatmap() {
  const [selectedPost, setSelectedPost] = useState<number | null>(null);
  const [selectedTech, setSelectedTech] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["techniques", "only_seen"],
    queryFn: () => api.techniques({ only_seen: true, limit: 1000 }),
  });

  const detail = useQuery({
    queryKey: ["technique", selectedTech],
    queryFn: () => api.technique(selectedTech!),
    enabled: selectedTech !== null,
  });

  const { columns, max, total } = useMemo(() => {
    const items = list.data?.items ?? [];
    const cols: Record<string, TechniqueListItem[]> = {};
    for (const t of TACTICS) cols[t.slug] = [];
    cols[UNCATEGORISED] = [];
    let mx = 0;
    for (const it of items) {
      mx = Math.max(mx, it.post_count);
      const tactics =
        it.tactics && it.tactics.length > 0 ? it.tactics : [UNCATEGORISED];
      for (const slug of tactics) {
        if (cols[slug]) cols[slug].push(it);
        else cols[UNCATEGORISED].push(it);
      }
    }
    for (const slug of Object.keys(cols)) {
      cols[slug].sort((a, b) => b.post_count - a.post_count);
    }
    return { columns: cols, max: mx, total: items.length };
  }, [list.data]);

  const hasUncat = columns[UNCATEGORISED]?.length > 0;
  const rows = hasUncat
    ? [
        ...TACTIC_ROWS,
        { phase: "Other", tactics: [{ slug: UNCATEGORISED, label: "Other" }] },
      ]
    : TACTIC_ROWS;

  return (
    <div className="mx-auto max-w-[1840px] px-4 pb-24 sm:px-8">
      <SectionDivider
        index="03"
        label="ATT&CK coverage"
        trailing={
          list.isLoading
            ? "Loading…"
            : `${total} techniques observed · busiest: ${max} posts`
        }
      />
      <p className="m-0 mb-6 max-w-[70ch] text-sm text-text-muted">
        Every technique mapped to at least one post, under each ATT&amp;CK tactic it belongs to.
        Darker cells have more posts; a technique can sit under several tactics. Select a cell to see
        its evidence and how each mapping was made.
      </p>
      {list.isError && (
        <QueryError what="techniques" error={list.error} onRetry={() => list.refetch()} />
      )}

      <div className="space-y-6">
        {rows.map((row) => (
          <div key={row.phase}>
            <h3 className="m-0 mb-2 text-sm font-bold">{row.phase}</h3>

            <div className="overflow-x-auto border border-text">
              <div
                className="grid gap-px bg-border-soft"
                style={{
                  gridTemplateColumns: `repeat(${row.tactics.length}, minmax(150px, 1fr))`,
                }}
              >
                {row.tactics.map((t) => (
                  <div
                    key={t.slug}
                    className="bg-surface-1 px-2.5 py-2.5 text-left text-xs font-bold leading-tight"
                  >
                    {t.label}
                    <span className="ml-1 font-normal text-text-muted tabular-nums">
                      {columns[t.slug]?.length ?? 0}
                    </span>
                  </div>
                ))}

                {row.tactics.map((t) => (
                  <div
                    key={`col-${t.slug}`}
                    className="flex flex-col gap-px bg-surface-1"
                  >
                    {columns[t.slug]?.map((it) => (
                      <button
                        key={`${t.slug}-${it.technique_id}`}
                        onClick={() => {
                          setSelectedTech(it.technique_id);
                          setSelectedPost(null);
                        }}
                        className={`px-2.5 py-1.5 text-left text-xs leading-tight outline-offset-[-2px] hover:outline hover:outline-2 hover:outline-accent ${
                          selectedTech === it.technique_id ? "outline outline-2 outline-accent" : ""
                        } ${onInk(it.post_count, max) ? "text-surface-1" : "text-text"}`}
                        style={{ backgroundColor: shade(it.post_count, max) }}
                        title={`${it.technique_id} — ${it.name ?? ""} (${it.post_count} posts)`}
                      >
                        <div className="flex items-baseline gap-1">
                          <span className="font-mono font-semibold">{it.technique_id}</span>
                          <span className="ml-auto tabular-nums">{it.post_count}</span>
                        </div>
                        <div className="truncate">{it.name ?? "—"}</div>
                      </button>
                    ))}
                    {columns[t.slug]?.length === 0 && (
                      <div className="px-2.5 py-2 text-xs text-text-muted">None observed</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-text-muted">
        <span>Posts per technique</span>
        <div className="flex items-center gap-px">
          {[0, 1, 2, 4, 8, 16, 32].map((n) => (
            <span
              key={n}
              className="w-6 h-3 inline-block"
              style={{ backgroundColor: shade(n, Math.max(32, max)) }}
              title={`${n}+`}
            />
          ))}
        </div>
        <span>fewer to more (log scale)</span>
      </div>

      <TechniquePanel
        techniqueId={selectedTech}
        loading={detail.isLoading}
        detail={detail.data ?? null}
        onClose={() => setSelectedTech(null)}
        onSelectPost={(id) => setSelectedPost(id)}
      />

      <DetailPanel id={selectedPost} onClose={() => setSelectedPost(null)} />

      <div className="h-32" />
    </div>
  );
}

function TechniquePanel({
  techniqueId,
  loading,
  detail,
  onClose,
  onSelectPost,
}: {
  techniqueId: string | null;
  loading: boolean;
  detail: import("../lib/api").TechniqueDetail | null;
  onClose: () => void;
  onSelectPost: (id: number) => void;
}) {
  if (techniqueId === null) return null;
  return (
    <aside
      role="dialog"
      aria-label={`Technique ${techniqueId}`}
      className="fixed top-0 right-0 bottom-0 z-30 w-[min(460px,100vw)] overflow-y-auto border-l-2 border-rule bg-surface-1 shadow-[-12px_0_32px_rgba(0,0,0,0.45)]"
    >
      <div className="px-6 py-5">
        <div className="flex items-center justify-between mb-4">
          <span className="font-mono text-sm text-text-muted">{techniqueId}</span>
          <button
            onClick={onClose}
            className="border border-text px-2.5 py-1 text-sm font-semibold hover:bg-text hover:text-surface-1"
          >
            Close
          </button>
        </div>

        {loading && (
          <p className="text-sm text-text-muted">Loading…</p>
        )}

        {detail && (
          <div className="space-y-5">
            <header>
              <h2 className="m-0 text-xl font-bold leading-tight">
                {detail.name ?? "—"}
              </h2>
              {detail.tactics && detail.tactics.length > 0 && (
                <div className="mt-1 text-sm text-text-muted">
                  {detail.tactics.join(" · ")}
                </div>
              )}
              {detail.url && (
                <a
                  href={detail.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-block text-sm font-semibold text-accent underline"
                >
                  Open on attack.mitre.org
                </a>
              )}
            </header>

            {detail.description && (
              <section>
                <SectionLabel>Description</SectionLabel>
                <p className="m-0 text-sm leading-relaxed line-clamp-[12]">
                  {detail.description}
                </p>
              </section>
            )}

            <section>
              <SectionLabel>Evidence: {detail.posts.length} mappings</SectionLabel>
              <div className="mb-3"><ProvenanceLegend compact /></div>
              <ul className="m-0 list-none p-0 text-sm">
                {detail.posts.map((p) => (
                  <li key={`${p.raw_post_id}-${p.source}`}>
                    <button
                      onClick={() => onSelectPost(p.raw_post_id)}
                      className="w-full border-t border-border-soft px-1 py-2 text-left hover:bg-surface-2"
                    >
                      <div className="flex items-baseline gap-2">
                        <span className="font-mono">#{p.raw_post_id}</span>
                        <span className="text-xs text-text-muted">{p.category}</span>
                        <span className="ml-auto">
                          <ProvenanceLabel source={p.source} />
                        </span>
                      </div>
                      <div className="mt-0.5 truncate font-semibold">
                        {p.thread_title}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </div>
    </aside>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="m-0 mb-2 border-t-2 border-rule pt-2 text-sm font-bold">{children}</h3>
  );
}
