import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type TechniqueListItem } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";

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
      { slug: "defense-evasion", label: "Defense Evasion" },
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

function shade(count: number, max: number): string {
  if (count <= 0) return "rgba(167, 139, 250, 0.04)";
  const t = Math.min(1, Math.log(1 + count) / Math.log(1 + max));
  const alpha = 0.12 + t * 0.78;
  return `rgba(167, 139, 250, ${alpha.toFixed(3)})`;
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
    <div className="max-w-[1840px] mx-auto px-8">
      <SectionDivider
        index="03"
        label="MITRE ATT&CK heatmap"
        trailing={
          list.isLoading
            ? "loading…"
            : `${total} techniques observed · max ${max} posts`
        }
      />

      <div className="space-y-6">
        {rows.map((row) => (
          <div key={row.phase}>
            <div className="mb-2 flex items-baseline gap-3 font-mono text-[10px] tracking-[0.22em] text-text-muted">
              <span className="text-accent">{`>`}</span>
              <span>{row.phase.toUpperCase()}</span>
              <span className="flex-1 border-t border-border-soft/60" />
            </div>

            <div className="border border-border-soft bg-surface-1/30 backdrop-blur-sm">
              <div
                className="grid gap-px bg-border-soft/60 p-px"
                style={{
                  gridTemplateColumns: `repeat(${row.tactics.length}, minmax(160px, 1fr))`,
                }}
              >
                {row.tactics.map((t) => (
                  <div
                    key={t.slug}
                    className="bg-base/80 px-2 py-3 font-mono text-[10px] tracking-[0.18em] text-accent text-center"
                  >
                    {t.label.toUpperCase()}
                    <div className="text-text-muted opacity-60 mt-0.5">
                      {columns[t.slug]?.length ?? 0}
                    </div>
                  </div>
                ))}

                {row.tactics.map((t) => (
                  <div
                    key={`col-${t.slug}`}
                    className="bg-base/40 flex flex-col gap-px"
                  >
                    {columns[t.slug]?.map((it) => (
                      <button
                        key={`${t.slug}-${it.technique_id}`}
                        onClick={() => {
                          setSelectedTech(it.technique_id);
                          setSelectedPost(null);
                        }}
                        className={`text-left px-2 py-1.5 font-mono text-[10px] leading-tight border-l-2 transition-colors hover:border-accent ${
                          selectedTech === it.technique_id
                            ? "border-accent"
                            : "border-transparent"
                        }`}
                        style={{ backgroundColor: shade(it.post_count, max) }}
                        title={`${it.technique_id} — ${it.name ?? ""} (${it.post_count} posts)`}
                      >
                        <div className="flex items-baseline gap-1">
                          <span className="text-accent font-semibold">
                            {it.technique_id}
                          </span>
                          <span className="ml-auto text-text tabular-nums opacity-80">
                            {it.post_count}
                          </span>
                        </div>
                        <div className="text-text/85 truncate">
                          {it.name ?? "—"}
                        </div>
                      </button>
                    ))}
                    {columns[t.slug]?.length === 0 && (
                      <div className="px-2 py-2 font-mono text-[10px] text-text-muted opacity-40 italic">
                        none
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-4 font-mono text-[10px] tracking-[0.15em] text-text-muted">
        <span>POST DENSITY</span>
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
        <span className="opacity-60">click a technique cell to inspect</span>
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
      className="fixed top-0 right-0 bottom-0 w-[min(460px,100vw)] z-30 border-l border-border-soft bg-base/95 backdrop-blur-md overflow-y-auto"
      style={{ boxShadow: "-12px 0 40px rgba(0,0,0,0.4)" }}
    >
      <div className="px-6 py-5">
        <div className="flex items-center justify-between mb-4">
          <span className="font-mono text-[11px] tracking-[0.2em] text-accent">
            {techniqueId}
          </span>
          <button
            onClick={onClose}
            className="font-mono text-xs text-text-muted hover:text-accent border border-border-soft px-2 py-1"
          >
            [ CLOSE ]
          </button>
        </div>

        {loading && (
          <div className="font-mono text-xs text-text-muted">loading…</div>
        )}

        {detail && (
          <div className="space-y-5">
            <header>
              <h2 className="font-display text-lg leading-tight">
                {detail.name ?? "—"}
              </h2>
              {detail.tactics && detail.tactics.length > 0 && (
                <div className="font-mono text-[10px] tracking-[0.15em] text-text-muted mt-1">
                  {detail.tactics.join(" · ")}
                </div>
              )}
              {detail.url && (
                <a
                  href={detail.url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono text-[10px] text-accent hover:underline"
                >
                  attack.mitre.org ↗
                </a>
              )}
            </header>

            {detail.description && (
              <section>
                <SectionLabel>DESCRIPTION</SectionLabel>
                <p className="text-xs leading-relaxed text-text/85 line-clamp-[12]">
                  {detail.description}
                </p>
              </section>
            )}

            <section>
              <SectionLabel>POSTS ({detail.posts.length})</SectionLabel>
              <ul className="space-y-1 font-mono text-xs">
                {detail.posts.map((p) => (
                  <li key={`${p.raw_post_id}-${p.source}`}>
                    <button
                      onClick={() => onSelectPost(p.raw_post_id)}
                      className="w-full text-left px-2 py-1.5 border border-transparent hover:border-accent hover:bg-accent/5 transition-colors"
                    >
                      <div className="flex items-baseline gap-2">
                        <span className="text-accent">#{p.raw_post_id}</span>
                        <span className="text-text-muted text-[10px]">
                          {p.category}
                        </span>
                        <span className="ml-auto text-text-muted text-[10px]">
                          {p.source.replace("llm_", "")}
                        </span>
                      </div>
                      <div className="text-text truncate mt-0.5">
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
    <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted mb-2">
      {children}
    </div>
  );
}
