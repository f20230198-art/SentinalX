import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type Investigation,
  type InvestigationMitigation,
  type Lens,
  type PostDetail,
} from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { CitationText } from "../components/CitationText";
import { QueryError } from "../components/Evidence";
import { intentColor } from "../lib/palette";

/* ----------------------------------------------------------------------- *
 * Investigations: saved filters over the corpus, each with an optional lens
 * summary whose [#id] citations open the cited post.
 *
 * Left: the case list. Right: the selected case file — filter, lens summary
 * (the payoff), priority mitigations, matched posts.
 * ----------------------------------------------------------------------- */

const EvidenceGraph = lazy(() =>
  import("../components/EvidenceGraph").then((m) => ({ default: m.EvidenceGraph })),
);

const btn =
  "border border-text px-3 py-1.5 text-sm font-semibold no-underline hover:bg-text hover:text-surface-1 disabled:cursor-not-allowed disabled:opacity-40";
const btnPrimary =
  "border border-accent bg-accent px-3 py-1.5 text-sm font-semibold text-surface-1 no-underline hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-40";

export function Investigations() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [postId, setPostId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);

  const list = useQuery({ queryKey: ["investigations"], queryFn: api.investigations });
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
  // Open the most recent case by default: an empty right pane is a dead end.
  useEffect(() => {
    if (selectedId === null && items.length > 0) setSelectedId(items[0].id);
  }, [items, selectedId]);

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-24 sm:px-8">
      <SectionDivider
        label="Investigations"
        trailing={list.isLoading ? "Loading…" : `${items.length} saved`}
      />

      <div className="grid grid-cols-12 gap-y-8 lg:gap-x-10">
        <aside className="col-span-12 min-w-0 lg:col-span-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="m-0 text-sm font-bold">Case files</h3>
            <button onClick={() => setCreating((v) => !v)} className={creating ? btn : btnPrimary}>
              {creating ? "Cancel" : "New investigation"}
            </button>
          </div>

          {creating && (
            <CreateForm
              lenses={lenses.data?.items ?? []}
              onCreated={(inv) => {
                qc.invalidateQueries({ queryKey: ["investigations"] });
                setSelectedId(inv.id);
                setCreating(false);
              }}
            />
          )}

          {list.isError && (
            <QueryError what="investigations" error={list.error} onRetry={() => list.refetch()} />
          )}

          <ul className="m-0 list-none p-0">
            {items.map((inv) => {
              const active = selectedId === inv.id;
              return (
                <li key={inv.id}>
                  <button
                    onClick={() => setSelectedId(inv.id)}
                    aria-current={active}
                    className={`w-full border-t py-3 pl-3 text-left ${
                      active
                        ? "border-t-rule bg-surface-1 shadow-[inset_3px_0_0_var(--color-accent)]"
                        : "border-t-border-soft hover:bg-surface-1"
                    }`}
                  >
                    <div className="flex items-baseline gap-2 pr-3">
                      <span className="font-mono text-xs text-text-muted">#{inv.id}</span>
                      <span className="truncate text-sm font-semibold">{inv.name}</span>
                      <span className="ml-auto whitespace-nowrap text-xs text-text-muted">
                        {lensLabel(inv.lens, lenses.data?.items)}
                      </span>
                    </div>
                    {inv.description && (
                      <div className="mt-1 line-clamp-2 pr-3 text-xs text-text-muted">{inv.description}</div>
                    )}
                  </button>
                </li>
              );
            })}
            {!list.isLoading && !list.isError && items.length === 0 && (
              <li className="border-t border-border-soft py-4 text-sm text-text-muted">
                No investigations yet. Create one to save a filter and get a cited lens summary.
              </li>
            )}
          </ul>
        </aside>

        <section className="col-span-12 min-w-0 lg:col-span-8">
          {selectedId === null ? (
            <div className="border border-dashed border-border-soft p-8 text-sm text-text-muted">
              Select a case file to read its lens summary. Every <span className="font-mono">[#id]</span> in
              the summary opens the post it cites.
            </div>
          ) : detail.isError ? (
            <QueryError what="this investigation" error={detail.error} onRetry={() => detail.refetch()} />
          ) : (
            <InvestigationDetail
              loading={detail.isLoading}
              data={detail.data ?? null}
              lenses={lenses.data?.items}
              onSelectPost={setPostId}
              onRerun={() => rerun.mutate(selectedId)}
              rerunLoading={rerun.isPending}
              rerunError={rerun.error}
              onDelete={() => {
                if (confirm("Delete this investigation? Its saved filter and summary will be removed."))
                  remove.mutate(selectedId);
              }}
            />
          )}
        </section>
      </div>

      <DetailPanel id={postId} onClose={() => setPostId(null)} />
    </div>
  );
}

function lensLabel(name: string | null, lenses: Lens[] | undefined): string {
  if (!name) return "No lens";
  return lenses?.find((l) => l.name === name)?.label ?? name;
}

function InvestigationDetail({
  loading,
  data,
  lenses,
  onSelectPost,
  onRerun,
  rerunLoading,
  rerunError,
  onDelete,
}: {
  loading: boolean;
  data: Investigation | null;
  lenses: Lens[] | undefined;
  onSelectPost: (id: number) => void;
  onRerun: () => void;
  rerunLoading: boolean;
  rerunError: Error | null;
  onDelete: () => void;
}) {
  // Hooks before any early return (rules of hooks).
  const filterEntries = useMemo(
    () => Object.entries(data?.filters ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== ""),
    [data?.filters],
  );

  if (loading || !data) return <p className="text-sm text-text-muted">Loading case file…</p>;

  return (
    <article className="border-t-2 border-rule">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-4">
        <span className="font-mono text-sm text-text-muted">#{data.id}</span>
        <h2 className="m-0 text-2xl font-extrabold leading-tight tracking-[-0.02em]">{data.name}</h2>
        <span className="ml-auto text-sm text-text-muted">{lensLabel(data.lens, lenses)} lens</span>
      </header>

      {data.description && <p className="m-0 mb-4 max-w-[70ch] text-sm text-text-muted">{data.description}</p>}

      <div className="flex flex-wrap gap-2 pb-6">
        <button onClick={onRerun} disabled={rerunLoading || !data.lens} className={btnPrimary}
          title={data.lens ? "Ask the local LLM to rewrite the summary over the current matches" : "Set a lens first"}>
          {rerunLoading ? "Running lens…" : data.summary ? "Rerun summary" : "Generate summary"}
        </button>
        <Link to={`/investigations/${data.id}/graph`} className={btn}>Case graph</Link>
        <a href={api.exportInvestigationUrl(data.id)} target="_blank" rel="noreferrer" className={btn}>
          Export PDF
        </a>
        <button onClick={onDelete} className="ml-auto px-3 py-1.5 text-sm font-semibold text-danger underline">
          Delete
        </button>
      </div>

      <div className="space-y-8">
        <section>
          <SectionLabel>Filter</SectionLabel>
          <p className="m-0 text-sm">
            {filterEntries.length === 0 ? (
              <span className="text-text-muted">No filter: matches every post.</span>
            ) : (
              filterEntries.map(([k, v], i) => (
                <span key={k}>
                  {i > 0 && <span className="text-text-muted"> and </span>}
                  <span className="text-text-muted">{k}</span> = <span className="font-mono font-semibold">{String(v)}</span>
                </span>
              ))
            )}
            <span className="ml-3 text-text-muted tabular-nums">{data.matched_total ?? 0} posts match now</span>
          </p>
        </section>

        <section>
          <SectionLabel>Lens summary</SectionLabel>
          {rerunError && (
            <div className="mb-3">
              <QueryError what="a new summary" error={rerunError} />
              <p className="m-0 mt-1 text-xs text-text-muted">The lens runs on the local LLM; it needs Ollama running.</p>
            </div>
          )}
          {data.summary ? (
            <div className="max-w-[75ch] whitespace-pre-wrap [overflow-wrap:anywhere] border-l-2 border-rule bg-surface-1 py-4 pr-5 pl-5 text-[15px] leading-relaxed">
              <CitationText text={data.summary} onSelect={onSelectPost} />
            </div>
          ) : (
            <p className="m-0 text-sm text-text-muted">
              No summary yet. Generate one to get a single report over the matched posts, with every claim cited.
            </p>
          )}
          {data.last_run_at && (
            <p className="m-0 mt-2 text-xs text-text-muted tabular-nums">
              Written {new Date(data.last_run_at * 1000).toISOString().replace("T", " ").slice(0, 16)} UTC by{" "}
              {data.summary_model ?? "—"} over {data.summary_post_ids.length} posts.
            </p>
          )}
        </section>

        {data.matched_posts && data.matched_posts.length > 0 && (
          <section>
            <SectionLabel>Evidence graph</SectionLabel>
            <InlineGraph ids={data.matched_posts.slice(0, 15).map((p) => p.id)} onSelectPost={onSelectPost} />
            <Link to={`/investigations/${data.id}/graph`} className="mt-2 inline-block text-sm font-semibold text-accent underline">
              Open full-screen graph ({Math.min(data.matched_total ?? 0, 25)} posts)
            </Link>
          </section>
        )}

        {data.mitigations && data.mitigations.length > 0 && (
          <section>
            <SectionLabel>Priority mitigations</SectionLabel>
            <p className="m-0 mb-3 max-w-[70ch] text-sm text-text-muted">
              Official MITRE ATT&amp;CK countermeasures, ranked by how many matched posts each one defends.
              Pure lookup, no LLM.
            </p>
            <ol className="m-0 list-none p-0">
              {data.mitigations.slice(0, 12).map((m) => (
                <MitigationRow key={m.mitigation_id} m={m} />
              ))}
            </ol>
          </section>
        )}

        {data.matched_posts && data.matched_posts.length > 0 && (
          <section>
            <SectionLabel>Matched posts ({data.matched_posts.length})</SectionLabel>
            <ul className="m-0 list-none p-0 text-sm">
              {data.matched_posts.slice(0, 25).map((p) => (
                <li key={p.id}>
                  <button onClick={() => onSelectPost(p.id)}
                    className="flex w-full items-baseline gap-3 border-t border-border-soft py-2 text-left hover:bg-surface-1">
                    <span className="w-12 font-mono text-text-muted">#{p.id}</span>
                    <span className="min-w-0 flex-1 truncate font-semibold">{p.thread_title}</span>
                    <span className="text-xs text-text-muted">{p.category}</span>
                    {p.intent && (
                      <span className="w-24 text-right text-xs font-semibold" style={{ color: intentColor(p.intent) }}>
                        {p.intent}
                      </span>
                    )}
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

function InlineGraph({ ids, onSelectPost }: { ids: number[]; onSelectPost: (id: number) => void }) {
  const qs = useQueries({ queries: ids.map((id) => ({ queryKey: ["post", id], queryFn: () => api.post(id) })) });
  const posts = qs.map((q) => q.data).filter((p): p is PostDetail => !!p);
  if (qs.some((q) => q.isLoading))
    return <div className="grid h-80 place-items-center border border-dashed border-border-soft text-sm text-text-muted">Drawing the evidence…</div>;
  return (
    <Suspense fallback={<div className="route-loading" />}>
      <EvidenceGraph posts={posts} onSelectPost={onSelectPost} height={460} />
    </Suspense>
  );
}

function CreateForm({ lenses, onCreated }: { lenses: Lens[]; onCreated: (inv: Investigation) => void }) {
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
      return api.createInvestigation({ name: name.trim(), filters, lens: lens || undefined });
    },
    onSuccess: onCreated,
    onError: (e: Error) => setError(e.message),
  });

  const input =
    "w-full border border-border-soft bg-surface-1 px-2.5 py-2 text-sm focus:border-text focus:outline-none";

  return (
    <form
      className="mb-4 space-y-3 border border-text bg-surface-1 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        if (!name.trim()) {
          setError("Give the investigation a name.");
          return;
        }
        create.mutate();
      }}
    >
      <label className="block">
        <span className="mb-1 block text-xs font-semibold">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Healthcare access sales" className={input} />
      </label>
      <fieldset className="m-0 border-0 p-0">
        <legend className="mb-1 text-xs font-semibold">Lens</legend>
        <div className="flex flex-wrap gap-1.5">
          {lenses.map((l) => (
            <button type="button" key={l.name} onClick={() => setLens(l.name)} aria-pressed={lens === l.name}
              title={l.description}
              className={`border px-2.5 py-1 text-xs font-semibold ${
                lens === l.name ? "border-text bg-text text-surface-1" : "border-border-soft text-text-muted hover:text-text"
              }`}>
              {l.label}
            </button>
          ))}
        </div>
      </fieldset>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold">Intent <span className="font-normal text-text-muted">optional</span></span>
          <select value={intent} onChange={(e) => setIntent(e.target.value)} className={input}>
            <option value="">Any</option>
            {["sale", "recruitment", "how-to", "doxxing", "discussion", "other"].map((i) => (
              <option key={i} value={i}>{i}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold">Keyword <span className="font-normal text-text-muted">optional</span></span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. VPN" className={input} />
        </label>
      </div>
      {error && <p role="alert" className="m-0 text-sm font-semibold text-danger">{error}</p>}
      <div className="flex justify-end">
        <button type="submit" disabled={create.isPending} className={btnPrimary}>
          {create.isPending ? "Creating…" : "Create investigation"}
        </button>
      </div>
    </form>
  );
}

function MitigationRow({ m }: { m: InvestigationMitigation }) {
  const pct = Math.round(m.post_share * 100);
  return (
    <li className="grid grid-cols-[4.5rem_1fr_auto] items-baseline gap-x-3 border-t border-border-soft py-2 text-sm">
      <span className="font-mono text-ok">{m.mitigation_id}</span>
      <span className="font-semibold">{m.name}</span>
      <span className="whitespace-nowrap text-xs text-text-muted tabular-nums">
        {m.posts_covered} posts · {pct}%
      </span>
      <span />
      <div className="col-span-2 mt-1 h-1.5 bg-surface-2">
        <div className="h-full bg-ok" style={{ width: `${pct}%` }} />
      </div>
      <span />
      <span className="col-span-2 mt-1 truncate text-xs text-text-muted" title={m.techniques.join(", ")}>
        Counters <span className="font-mono">{m.techniques.join(" ")}</span>
      </span>
    </li>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <h3 className="m-0 mb-3 text-sm font-bold">{children}</h3>;
}
