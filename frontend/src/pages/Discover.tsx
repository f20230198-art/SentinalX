import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DiscoverResult } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { QueryError } from "../components/Evidence";

/* Discover page: search our DB + dark-web engines, then send new results to the pipeline */

// Example searches shown under the search box
const EXAMPLES = ["okta vpn access", "ransomware loader", "CVE-2024-3400", "healthcare database dump"];

// Discover page
export function Discover() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  // Search text (kept in the URL), engine/LLM options, ticked results, open post
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [useReal, setUseReal] = useState(false);
  const [refine, setRefine] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [openPost, setOpenPost] = useState<number | null>(null);

  // Check Tor and Ollama so their options can be turned off when offline
  const health = useQuery({ queryKey: ["healthz", "full"], queryFn: api.healthzFull });
  const torUp = health.data?.checks.tor_socks?.status === "up";
  const llmUp = health.data?.checks.ollama?.status === "up";

  // Run a search (local DB, plus Ahmia if ticked); clear old selections afterwards
  const search = useMutation({
    mutationFn: (q: string) =>
      api.discover({ query: q, engines: useReal ? ["local", "ahmia"] : ["local"], refine: refine && llmUp }),
    onSuccess: () => {
      setPicked(new Set());
      watch.reset();
    },
  });

  const qc = useQueryClient();
  // Save the query as a watchlist so future posts raise alerts
  const watch = useMutation({
    mutationFn: (q: string) => api.createWatchlist({ name: q, terms: [q] }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlists"] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  // Send the ticked pages to the pipeline, then jump to Scout to watch progress
  const send = useMutation({
    mutationFn: (urls: string[]) => api.sendPages(urls, !llmUp),
    onSuccess: (job) => navigate(`/scout?job=${job.id}`),
  });

  // Start a search (needs at least 2 characters)
  const run = (q: string) => {
    const v = q.trim();
    if (v.length < 2) return;
    setQuery(v);
    setParams({ q: v }, { replace: true });
    search.mutate(v);
  };

  // Split results: already collected vs new on the dark web
  const { known, fresh } = useMemo(() => {
    const rs = search.data?.results ?? [];
    return { known: rs.filter((r) => r.engine === "local"), fresh: rs.filter((r) => r.engine !== "local") };
  }, [search.data]);

  // Tick / untick a result
  const toggle = (url: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(url) ? next.delete(url) : next.add(url);
      return next;
    });

  return (
    <div className="mx-auto max-w-[1100px] px-4 pb-24 sm:px-8">
      <SectionDivider label="Discover" trailing="Search before you scrape" />

      {/* Search box, example queries, and options */}
      <form
        className="border border-text bg-surface-1 p-5"
        onSubmit={(e) => {
          e.preventDefault();
          run(query);
        }}
      >
        <label htmlFor="discover-q" className="mb-2 block text-sm font-bold">
          What are you looking for?
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="discover-q"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="A company, product, CVE, wallet, or a question like “who is selling VPN access to hospitals?”"
            className="min-w-0 flex-1 border border-border-soft bg-surface-1 px-3 py-2 text-sm focus:border-text focus:outline-none"
          />
          <button
            type="submit"
            disabled={search.isPending || query.trim().length < 2}
            className="whitespace-nowrap border border-accent bg-accent px-4 py-2 text-sm font-semibold text-surface-1 hover:bg-accent-strong disabled:opacity-40"
          >
            {search.isPending ? (useReal ? "Searching over Tor…" : "Searching…") : "Search"}
          </button>
        </div>

        <div className="mt-3 flex flex-wrap gap-2 text-sm">
          <span className="text-text-muted">Try:</span>
          {EXAMPLES.map((ex) => (
            <button type="button" key={ex} onClick={() => run(ex)} className="text-accent underline hover:text-accent-strong">
              {ex}
            </button>
          ))}
        </div>

        <fieldset className="m-0 mt-4 grid gap-3 border-0 border-t border-border-soft p-0 pt-4 sm:grid-cols-2">
          <legend className="sr-only">Search options</legend>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked disabled className="mt-1" />
            <span>
              <span className="font-semibold">SentinelX index</span>
              <span className="block text-text-muted">Everything already collected. Offline and instant.</span>
            </span>
          </label>
          <label className={`flex items-start gap-2 text-sm ${torUp ? "cursor-pointer" : "opacity-70"}`}>
            <input type="checkbox" checked={useReal && torUp} disabled={!torUp} onChange={(e) => setUseReal(e.target.checked)} className="mt-1 accent-[var(--color-text)]" />
            <span>
              <span className="font-semibold">Real dark-web engines (Ahmia, over Tor)</span>
              <span className="block text-text-muted">
                {torUp
                  ? "Read-only search through Tor. Results can include real criminal sites; only their pages are read, nothing is downloaded or submitted."
                  : "Unavailable: the Tor proxy is offline. Start it with docker compose up -d."}
              </span>
            </span>
          </label>
          <label className={`flex items-start gap-2 text-sm sm:col-span-2 ${llmUp ? "cursor-pointer" : "opacity-70"}`}>
            <input type="checkbox" checked={refine && llmUp} disabled={!llmUp} onChange={(e) => setRefine(e.target.checked)} className="mt-1 accent-[var(--color-text)]" />
            <span>
              <span className="font-semibold">Refine my question with the local LLM</span>
              <span className="block text-text-muted">
                {llmUp ? "Turns a question into search keywords before searching." : "Unavailable: Ollama is offline. Searching with your words as typed."}
              </span>
            </span>
          </label>
        </fieldset>
      </form>

      {/* Search failed */}
      {search.isError && (
        <div className="mt-6">
          <QueryError what="search results" error={search.error} onRetry={() => run(query)} />
        </div>
      )}

      {/* Results */}
      {search.data && (
        <div className="mt-8 space-y-10" aria-live="polite">
          {(search.data.refined || search.data.refine_error) && (
            <p className="m-0 text-sm text-text-muted">
              {search.data.refined ? (
                <>Searched for <span className="font-mono text-text">{search.data.refined}</span> (refined by the local LLM).</>
              ) : (
                search.data.refine_error
              )}
            </p>
          )}
          {/* Offer to watch this query */}
          <div className="flex flex-wrap items-center gap-3 border border-border-soft bg-surface-1 px-4 py-3 text-sm">
            <span>
              Keep an eye on <span className="font-semibold">“{search.data.query}”</span>: future posts that mention it
              raise an alert.
            </span>
            {watch.isSuccess ? (
              <Link to="/alerts" className="ml-auto font-semibold text-ok underline">Watching. Open alerts</Link>
            ) : (
              <button
                onClick={() => watch.mutate(search.data!.query)}
                disabled={watch.isPending}
                className="ml-auto border border-text px-3 py-1 font-semibold hover:bg-text hover:text-surface-1 disabled:opacity-40"
              >
                {watch.isPending ? "Saving…" : "Watch these words"}
              </button>
            )}
          </div>
          {/* Per-engine errors (e.g. Tor offline) */}
          {Object.entries(search.data.errors).map(([engine, msg]) => (
            <p key={engine} className="m-0 border-l-2 border-warn pl-3 text-sm text-warn">
              <span className="font-semibold">{engine}:</span> {msg}
            </p>
          ))}

          {/* Result counts for both groups */}
          <p className="m-0 text-sm text-text-muted">
            <span className="font-semibold text-text tabular-nums">{known.length}</span> already collected
            {useReal && (
              <>
                {" · "}
                <a href="#dark-web-results" className="font-semibold text-accent underline tabular-nums">
                  {fresh.length} new on the dark web ↓
                </a>
              </>
            )}
          </p>

          {/* Posts we already have: click to open */}
          <ResultGroup
            title="Already in your corpus"
            meta={`${known.length} matches`}
            empty="Nothing collected so far matches. Try the real engines, or broaden the words."
            results={known}
            render={(r) => (
              <button onClick={() => r.post_id && setOpenPost(r.post_id)} className="w-full py-3 text-left hover:bg-surface-1">
                <ResultBody r={r} />
              </button>
            )}
          />

          {/* New dark-web pages: tick to send to the pipeline */}
          {useReal && (
            <div id="dark-web-results" className="scroll-mt-24">
            <ResultGroup
              title="New on the dark web"
              meta={`${fresh.length} pages`}
              empty="The engines returned no .onion pages for this query."
              results={fresh}
              render={(r) => (
                <label className="flex cursor-pointer items-start gap-3 py-3 hover:bg-surface-1">
                  <input type="checkbox" checked={picked.has(r.url)} onChange={() => toggle(r.url)} className="mt-1.5 accent-[var(--color-text)]" />
                  <ResultBody r={r} />
                </label>
              )}
            />
            </div>
          )}
        </div>
      )}

      {/* Bar that appears when pages are ticked */}
      {picked.size > 0 && (
        <div className="sticky bottom-4 mt-6 flex flex-wrap items-center gap-3 border-2 border-rule bg-surface-1 p-4 shadow-[0_8px_24px_rgba(0,0,0,0.5)]">
          <span className="text-sm font-semibold">{picked.size} page{picked.size > 1 ? "s" : ""} selected</span>
          <span className="text-sm text-text-muted">
            Each page is read over Tor, then goes through IOC extraction, {llmUp ? "the LLM," : "(LLM skipped: offline)"} and ATT&amp;CK mapping.
          </span>
          {send.isError && <span className="text-sm text-danger">{(send.error as Error).message}</span>}
          <button
            onClick={() => send.mutate([...picked])}
            disabled={send.isPending}
            className="ml-auto border border-accent bg-accent px-4 py-2 text-sm font-semibold text-surface-1 hover:bg-accent-strong disabled:opacity-40"
          >
            {send.isPending ? "Starting…" : "Send to pipeline"}
          </button>
        </div>
      )}

      <DetailPanel id={openPost} onClose={() => setOpenPost(null)} />
    </div>
  );
}

// A titled list of results; `render` decides how each row looks
function ResultGroup({
  title,
  meta,
  empty,
  results,
  render,
}: {
  title: string;
  meta: string;
  empty: string;
  results: DiscoverResult[];
  render: (r: DiscoverResult) => React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-1 flex items-baseline border-b-2 border-rule pb-1.5">
        <h3 className="m-0 text-[1rem] font-bold">{title}</h3>
        <span className="ml-auto text-sm text-text-muted tabular-nums">{meta}</span>
      </div>
      {results.length === 0 ? (
        <p className="py-4 text-sm text-text-muted">{empty}</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {results.map((r) => (
            <li key={r.engine + r.url} className="border-b border-border-soft">
              {render(r)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Title, location and snippet. «» marks from the index become highlights. */
function ResultBody({ r }: { r: DiscoverResult }) {
  // Split the snippet so «matched words» can be highlighted
  const parts = r.snippet.split(/(«[^»]*»)/g);
  return (
    <span className="block min-w-0 flex-1">
      <span className="flex items-baseline gap-2">
        <span className="font-semibold">{r.title}</span>
        {r.post_id && <span className="font-mono text-xs text-text-muted">#{r.post_id}</span>}
      </span>
      <span className="block truncate font-mono text-xs text-text-muted">{r.post_id ? r.onion ?? "collected forum" : r.url}</span>
      {r.snippet && (
        <span className="mt-1 block text-sm text-text-muted">
          {parts.map((p, i) =>
            p.startsWith("«") ? (
              <mark key={i} className="bg-transparent font-semibold text-text underline decoration-accent decoration-2 underline-offset-2">
                {p.slice(1, -1)}
              </mark>
            ) : (
              <span key={i}>{p}</span>
            ),
          )}
        </span>
      )}
    </span>
  );
}
