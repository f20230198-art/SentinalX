import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { api, type Investigation, type Lens } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { CitationText } from "../components/CitationText";

/* ----------------------------------------------------------------------- *
 * Investigations page.
 *
 * Left column: investigation list (compact rows).
 * Right column: selected investigation — filters, matched posts, lens
 *   summary with [#NNN] citations rendered as clickable post links.
 *
 * Bottom sheet: a small "create" form (name + lens + simple filter chips).
 * ----------------------------------------------------------------------- */

export function Investigations() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [postId, setPostId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);

  const list = useQuery({
    queryKey: ["investigations"],
    queryFn: api.investigations,
  });

  const lenses = useQuery({ queryKey: ["lenses"], queryFn: api.lenses });

  const detail = useQuery({
    queryKey: ["investigation", selectedId],
    queryFn: () => api.investigation(selectedId!),
    enabled: selectedId !== null,
  });

  const rerun = useMutation({
    mutationFn: (id: number) => api.rerun(id),
    onSuccess: (data) => {
      qc.setQueryData(["investigation", data.id], data);
      qc.invalidateQueries({ queryKey: ["investigations"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteInvestigation(id),
    onSuccess: (_v, id) => {
      qc.invalidateQueries({ queryKey: ["investigations"] });
      if (selectedId === id) setSelectedId(null);
    },
  });

  const items = list.data?.items ?? [];

  return (
    <div className="max-w-[1840px] mx-auto px-8">
      <SectionDivider
        index="04"
        label="Investigations"
        trailing={
          list.isLoading
            ? "loading…"
            : `${items.length} saved · click to inspect`
        }
      />

      <div className="grid grid-cols-12 gap-6">
        <aside className="col-span-12 lg:col-span-4">
          <div className="mb-3 flex items-center justify-between font-mono text-[10px] tracking-[0.2em] text-text-muted">
            <span>SAVED</span>
            <button
              onClick={() => setCreating((v) => !v)}
              className={`px-2 py-1 border transition-colors ${
                creating
                  ? "border-accent text-accent"
                  : "border-border-soft hover:border-accent hover:text-accent"
              }`}
            >
              [ {creating ? "CANCEL" : "+ NEW"} ]
            </button>
          </div>

          <AnimatePresence>
            {creating && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden mb-3"
              >
                <CreateForm
                  lenses={lenses.data?.items ?? []}
                  onClose={() => setCreating(false)}
                  onCreated={(inv) => {
                    qc.invalidateQueries({ queryKey: ["investigations"] });
                    setSelectedId(inv.id);
                    setCreating(false);
                  }}
                />
              </motion.div>
            )}
          </AnimatePresence>

          <ul className="space-y-2">
            {items.map((inv) => (
              <li key={inv.id}>
                <button
                  onClick={() => setSelectedId(inv.id)}
                  className={`w-full text-left border px-3 py-2.5 transition-colors ${
                    selectedId === inv.id
                      ? "border-accent bg-accent/5"
                      : "border-border-soft hover:border-text-muted"
                  }`}
                >
                  <div className="flex items-baseline gap-2">
                    <span className="font-mono text-[10px] text-text-muted">
                      #{inv.id}
                    </span>
                    <span className="font-display text-sm truncate">
                      {inv.name}
                    </span>
                    <span className="ml-auto font-mono text-[10px] text-accent tracking-[0.15em]">
                      {inv.lens?.toUpperCase() ?? "—"}
                    </span>
                  </div>
                  {inv.description && (
                    <div className="mt-1 text-[11px] text-text-muted line-clamp-2">
                      {inv.description}
                    </div>
                  )}
                </button>
              </li>
            ))}
            {!list.isLoading && items.length === 0 && (
              <li className="font-mono text-xs text-text-muted opacity-60 italic">
                no investigations yet — start one with [+ NEW]
              </li>
            )}
          </ul>
        </aside>

        <section className="col-span-12 lg:col-span-8">
          {selectedId === null && (
            <div className="border border-dashed border-border-soft p-8 font-mono text-xs text-text-muted">
              select an investigation on the left to view its lens summary.
            </div>
          )}

          {selectedId !== null && (
            <InvestigationDetail
              loading={detail.isLoading}
              data={detail.data ?? null}
              onSelectPost={setPostId}
              onRerun={() => rerun.mutate(selectedId)}
              rerunLoading={rerun.isPending}
              onDelete={() => {
                if (confirm("delete this investigation?"))
                  remove.mutate(selectedId);
              }}
            />
          )}
        </section>
      </div>

      <DetailPanel id={postId} onClose={() => setPostId(null)} />

      <div className="h-32" />
    </div>
  );
}

function InvestigationDetail({
  loading,
  data,
  onSelectPost,
  onRerun,
  rerunLoading,
  onDelete,
}: {
  loading: boolean;
  data: Investigation | null;
  onSelectPost: (id: number) => void;
  onRerun: () => void;
  rerunLoading: boolean;
  onDelete: () => void;
}) {
  if (loading || !data)
    return <div className="font-mono text-xs text-text-muted">loading…</div>;

  const filterEntries = useMemo(
    () =>
      Object.entries(data.filters).filter(
        ([, v]) => v !== null && v !== undefined && v !== "",
      ),
    [data.filters],
  );

  return (
    <article className="border border-border-soft bg-surface-1/30 backdrop-blur-sm">
      <header className="px-5 py-4 border-b border-border-soft flex items-baseline gap-3">
        <span className="font-mono text-[10px] text-text-muted">
          #{data.id}
        </span>
        <h2 className="font-display text-lg leading-tight">{data.name}</h2>
        <span className="ml-auto font-mono text-[10px] text-accent tracking-[0.18em]">
          {data.lens?.toUpperCase() ?? "NO LENS"}
        </span>
      </header>

      <div className="px-5 py-4 space-y-5">
        {data.description && (
          <p className="text-sm text-text-muted leading-relaxed">
            {data.description}
          </p>
        )}

        <section>
          <SectionLabel>FILTERS</SectionLabel>
          {filterEntries.length === 0 ? (
            <div className="font-mono text-[11px] text-text-muted opacity-60">
              none — matches every post
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {filterEntries.map(([k, v]) => (
                <span
                  key={k}
                  className="font-mono text-[11px] border border-border-soft px-2 py-0.5"
                >
                  <span className="text-text-muted">{k}:</span>{" "}
                  <span className="text-accent">{String(v)}</span>
                </span>
              ))}
            </div>
          )}
          <div className="mt-2 font-mono text-[10px] text-text-muted">
            matched: {data.matched_total ?? 0} posts
          </div>
        </section>

        <section>
          <div className="flex items-center justify-between mb-2">
            <SectionLabel>LENS SUMMARY</SectionLabel>
            <div className="flex items-center gap-2">
              <button
                onClick={onRerun}
                disabled={rerunLoading || !data.lens}
                className="font-mono text-[10px] tracking-[0.2em] border border-border-soft px-2 py-1 hover:text-accent hover:border-accent disabled:opacity-30 transition-colors"
                title={data.lens ? "re-run the lens summary" : "no lens set"}
              >
                [ {rerunLoading ? "RUNNING…" : "RERUN"} ]
              </button>
              <Link
                to={`/investigations/${data.id}/graph`}
                className="font-mono text-[10px] tracking-[0.2em] border border-border-soft px-2 py-1 hover:text-accent hover:border-accent transition-colors"
                title="view the case-file graph (posts + IOCs + MITRE)"
              >
                [ CASE GRAPH ]
              </Link>
              <a
                href={api.exportInvestigationUrl(data.id)}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-[10px] tracking-[0.2em] border border-border-soft px-2 py-1 hover:text-accent hover:border-accent transition-colors"
                title="download a styled PDF of this investigation"
              >
                [ EXPORT PDF ]
              </a>
              <button
                onClick={onDelete}
                className="font-mono text-[10px] tracking-[0.2em] border border-border-soft px-2 py-1 hover:text-danger hover:border-danger transition-colors"
              >
                [ DELETE ]
              </button>
            </div>
          </div>

          {data.summary ? (
            <div className="text-sm leading-relaxed whitespace-pre-wrap text-text/90 bg-surface-1/40 border border-border-soft p-4">
              <CitationText text={data.summary} onSelect={onSelectPost} />
            </div>
          ) : (
            <div className="font-mono text-[11px] text-text-muted opacity-60">
              not yet run — hit [ RERUN ] to generate.
            </div>
          )}

          {data.last_run_at && (
            <div className="mt-2 font-mono text-[10px] text-text-muted">
              last run:{" "}
              {new Date(data.last_run_at * 1000)
                .toISOString()
                .replace("T", " ")
                .slice(0, 19)}{" "}
              · model: {data.summary_model ?? "—"} · over{" "}
              {data.summary_post_ids.length} posts
            </div>
          )}
        </section>

        {data.matched_posts && data.matched_posts.length > 0 && (
          <section>
            <SectionLabel>MATCHED POSTS ({data.matched_posts.length})</SectionLabel>
            <ul className="space-y-1 font-mono text-xs">
              {data.matched_posts.slice(0, 25).map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => onSelectPost(p.id)}
                    className="w-full text-left px-2 py-1.5 border border-transparent hover:border-accent hover:bg-accent/5 transition-colors"
                  >
                    <div className="flex items-baseline gap-2">
                      <span className="text-accent">#{p.id}</span>
                      <span className="text-text-muted text-[10px]">
                        {p.category}
                      </span>
                      {p.intent && (
                        <span className="ml-auto text-warn text-[10px]">
                          {p.intent}
                        </span>
                      )}
                    </div>
                    <div className="text-text truncate mt-0.5">
                      {p.thread_title}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </article>
  );
}

function CreateForm({
  lenses,
  onClose,
  onCreated,
}: {
  lenses: Lens[];
  onClose: () => void;
  onCreated: (inv: Investigation) => void;
}) {
  const [name, setName] = useState("");
  const [lens, setLens] = useState<string>(lenses[0]?.name ?? "");
  const [intent, setIntent] = useState("");
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => {
      const filters: Record<string, unknown> = {};
      if (intent) filters.intent = intent;
      if (q) filters.q = q;
      return api.createInvestigation({
        name: name.trim(),
        filters,
        lens: lens || undefined,
      });
    },
    onSuccess: onCreated,
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="border border-accent/50 bg-accent/5 p-3 space-y-3">
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="investigation name"
        className="w-full bg-base/60 border border-border-soft px-2 py-1.5 font-mono text-xs focus:border-accent outline-none"
      />
      <div>
        <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted mb-1">
          LENS
        </div>
        <div className="flex flex-wrap gap-1">
          {lenses.map((l) => (
            <button
              key={l.name}
              onClick={() => setLens(l.name)}
              className={`font-mono text-[10px] tracking-[0.15em] px-2 py-1 border transition-colors ${
                lens === l.name
                  ? "border-accent text-accent bg-accent/10"
                  : "border-border-soft text-text-muted hover:text-text"
              }`}
              title={l.description}
            >
              {l.label.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          placeholder="intent (optional)"
          className="bg-base/60 border border-border-soft px-2 py-1.5 font-mono text-xs focus:border-accent outline-none"
        />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="keyword (optional)"
          className="bg-base/60 border border-border-soft px-2 py-1.5 font-mono text-xs focus:border-accent outline-none"
        />
      </div>
      {error && (
        <div className="font-mono text-[10px] text-danger">{error}</div>
      )}
      <div className="flex justify-end gap-2">
        <button
          onClick={onClose}
          className="font-mono text-[10px] tracking-[0.2em] border border-border-soft px-2 py-1 hover:text-text transition-colors"
        >
          [ CANCEL ]
        </button>
        <button
          onClick={() => {
            setError(null);
            if (!name.trim()) {
              setError("name required");
              return;
            }
            create.mutate();
          }}
          disabled={create.isPending}
          className="font-mono text-[10px] tracking-[0.2em] border border-accent text-accent px-2 py-1 hover:bg-accent/10 disabled:opacity-30 transition-colors"
        >
          [ {create.isPending ? "CREATING…" : "CREATE"} ]
        </button>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted mb-2">
      {children}
    </div>
  );
}
