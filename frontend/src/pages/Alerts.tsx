import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { QueryError } from "../components/Evidence";
import { intentColor } from "../lib/palette";

/* Alerts page: watchlists on the left, posts that mention watched terms on the right */

// Alerts page
export function Alerts() {
  const qc = useQueryClient();
  // Watchlist filter (null = all), open post, and the new-watchlist form fields
  const [filterId, setFilterId] = useState<number | null>(null);
  const [openPost, setOpenPost] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [terms, setTerms] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  // Load watchlists, and alerts every 20s
  const lists = useQuery({ queryKey: ["watchlists"], queryFn: api.watchlists });
  const alerts = useQuery({
    queryKey: ["alerts", filterId],
    queryFn: () => api.alerts(filterId ?? undefined),
    refetchInterval: 20_000,
  });
  // Reload both lists after any change
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["watchlists"] });
    qc.invalidateQueries({ queryKey: ["alerts"] });
  };

  // Create a watchlist from the form ("a, b, c" -> ["a", "b", "c"])
  const create = useMutation({
    mutationFn: () =>
      api.createWatchlist({ name: name.trim(), terms: terms.split(",").map((t) => t.trim()).filter(Boolean) }),
    onSuccess: () => {
      setName("");
      setTerms("");
      setFormError(null);
      refresh();
    },
    onError: (e: Error) => setFormError(e.message),
  });
  // Delete a watchlist / mark alerts as seen
  const remove = useMutation({ mutationFn: api.deleteWatchlist, onSuccess: refresh });
  const seen = useMutation({ mutationFn: (ids?: number[]) => api.markAlertsSeen(ids), onSuccess: refresh });

  // Alerts not yet seen
  const items = alerts.data?.items ?? [];
  const unseen = items.filter((a) => !a.seen);

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-24 sm:px-8">
      <SectionDivider
        label="Alerts"
        trailing={alerts.data ? `${alerts.data.unseen} unseen · checked every 20 s` : "Loading…"}
      />

      <div className="grid grid-cols-12 gap-y-8 lg:gap-x-10">
        <aside className="col-span-12 min-w-0 lg:col-span-4">
          {/* New watchlist form */}
          <form
            className="mb-6 space-y-3 border border-text bg-surface-1 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <h3 className="m-0 text-sm font-bold">New watchlist</h3>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold">Name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Our company"
                className="w-full border border-border-soft bg-surface-1 px-2.5 py-2 text-sm focus:border-text focus:outline-none" />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold">
                Terms <span className="font-normal text-text-muted">comma-separated</span>
              </span>
              <input value={terms} onChange={(e) => setTerms(e.target.value)}
                placeholder="acme corp, acme-vpn.online, bc1q…"
                className="w-full border border-border-soft bg-surface-1 px-2.5 py-2 font-mono text-sm focus:border-text focus:outline-none" />
            </label>
            {formError && <p role="alert" className="m-0 text-sm font-semibold text-danger">{formError}</p>}
            <p className="m-0 text-xs text-text-muted">
              Past mentions are shown straight away as history. Only posts collected after this point raise new alerts.
            </p>
            <button type="submit" disabled={create.isPending || !terms.trim()}
              className="border border-accent bg-accent px-3 py-1.5 text-sm font-semibold text-surface-1 hover:bg-accent-strong disabled:opacity-40">
              {create.isPending ? "Saving…" : "Start watching"}
            </button>
          </form>

          <h3 className="m-0 mb-2 text-sm font-bold">Watchlists</h3>
          {lists.isError && <QueryError what="watchlists" error={lists.error} onRetry={() => lists.refetch()} />}
          {/* Watchlists: click one to filter the alerts */}
          <ul className="m-0 list-none p-0">
            <li>
              <button onClick={() => setFilterId(null)} aria-current={filterId === null}
                className={`w-full border-t border-border-soft py-2.5 pl-3 text-left text-sm font-semibold ${filterId === null ? "bg-surface-1 shadow-[inset_3px_0_0_var(--color-accent)]" : "hover:bg-surface-1"}`}>
                All watchlists
              </button>
            </li>
            {lists.data?.items.map((w) => (
              <li key={w.id} className="border-t border-border-soft">
                <div className={`flex items-start gap-2 py-2.5 pl-3 ${filterId === w.id ? "bg-surface-1 shadow-[inset_3px_0_0_var(--color-accent)]" : ""}`}>
                  <button onClick={() => setFilterId(w.id)} aria-current={filterId === w.id} className="min-w-0 flex-1 text-left">
                    <span className="flex items-baseline gap-2">
                      <span className="truncate text-sm font-semibold">{w.name}</span>
                      {w.unseen > 0 && (
                        <span className="bg-accent px-1.5 text-xs font-bold text-surface-1 tabular-nums">{w.unseen} new</span>
                      )}
                      <span className="ml-auto pr-2 text-xs text-text-muted tabular-nums">{w.hits} total</span>
                    </span>
                    <span className="mt-0.5 block truncate font-mono text-xs text-text-muted">{w.terms.join(", ")}</span>
                  </button>
                  <button
                    onClick={() => confirm(`Stop watching "${w.name}"? Its alerts are removed.`) && remove.mutate(w.id)}
                    className="pr-3 text-xs text-text-muted underline hover:text-danger"
                    aria-label={`Delete watchlist ${w.name}`}
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
            {lists.data && lists.data.items.length === 0 && (
              <li className="border-t border-border-soft py-3 text-sm text-text-muted">
                No watchlists yet. Add your organisation's name and domains above.
              </li>
            )}
          </ul>
        </aside>

        <section className="col-span-12 min-w-0 lg:col-span-8">
          {/* Header + "mark all as seen" */}
          <div className="mb-2 flex items-baseline border-b-2 border-rule pb-1.5">
            <h3 className="m-0 text-[1rem] font-bold">Mentions</h3>
            {unseen.length > 0 && (
              <button onClick={() => seen.mutate(undefined)} className="ml-auto text-sm font-semibold text-accent underline">
                Mark all {unseen.length} as seen
              </button>
            )}
          </div>
          {alerts.isError && <QueryError what="alerts" error={alerts.error} onRetry={() => alerts.refetch()} />}
          {alerts.data && items.length === 0 && (
            <p className="py-6 text-sm text-text-muted">
              {lists.data?.items.length ? "No post mentions these terms yet." : "Create a watchlist to start getting alerts."}
            </p>
          )}
          {/* One row per alert; opening it marks it seen */}
          <ul className="m-0 list-none p-0">
            {items.map((a) => (
              <li key={a.id}>
                <button
                  onClick={() => {
                    setOpenPost(a.raw_post_id);
                    if (!a.seen) seen.mutate([a.id]);
                  }}
                  className={`grid w-full grid-cols-[1fr_auto] gap-x-4 border-b border-border-soft py-3 pl-3 text-left hover:bg-surface-1 ${
                    a.seen ? "" : "bg-surface-1 shadow-[inset_3px_0_0_var(--color-accent)]"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="flex items-baseline gap-2">
                      {!a.seen && <span className="text-xs font-bold text-accent">New</span>}
                      <span className="truncate font-semibold">{a.thread_title}</span>
                      <span className="font-mono text-xs text-text-muted">#{a.raw_post_id}</span>
                    </span>
                    <span className="mt-1 block text-sm text-text-muted">
                      {a.watchlist} · matched{" "}
                      {a.matched_terms.map((t, i) => (
                        <span key={t}>
                          {i > 0 && ", "}
                          <span className="font-mono text-text">{t}</span>
                        </span>
                      ))}{" "}
                      · {a.category}
                    </span>
                  </span>
                  <span className="pr-3 text-right text-sm">
                    <span className="block font-semibold" style={{ color: intentColor(a.intent) }}>{a.intent ?? "—"}</span>
                    <span className="block text-xs text-text-muted tabular-nums">
                      {new Date(a.source_created_at * 1000).toISOString().slice(0, 10)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <DetailPanel id={openPost} onClose={() => setOpenPost(null)} />
    </div>
  );
}
