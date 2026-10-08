import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ScrapeJob } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { QueryError } from "../components/Evidence";

/* ----------------------------------------------------------------------- *
 * Scout — point SentinelX at an arbitrary darknet forum.
 *
 * Paste a .onion URL, hit RUN PIPELINE, and the backend scrapes it (HTML),
 * extracts IOCs, enriches with the local LLM, and maps it to MITRE ATT&CK —
 * all on demand, for a forum it has never seen before. The job runs in a
 * backend thread; this page polls its status and renders live progress.
 * ----------------------------------------------------------------------- */

// The pipeline stages, in order — drives the progress checklist.
const STAGES: { key: string; label: string }[] = [
  { key: "scraping", label: "Scrape the forum (HTML over Tor)" },
  { key: "extracting", label: "Extract IOCs + entities" },
  { key: "llm", label: "LLM enrichment" },
  { key: "mitre", label: "Map to MITRE ATT&CK" },
  { key: "done", label: "Done" },
];

// Where a given stage sits in the ordered list (-1 if unknown / queued).
function stageIndex(stage: string): number {
  // checking-ollama is a sub-step of the llm stage.
  const s = stage === "checking-ollama" ? "llm" : stage;
  return STAGES.findIndex((x) => x.key === s);
}

export function Scout() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [skipLlm, setSkipLlm] = useState(false);
  const [params] = useSearchParams();
  const [activeJobId, setActiveJobId] = useState<number | null>(
    params.get("job") ? Number(params.get("job")) : null,
  );
  const [formError, setFormError] = useState<string | null>(null);

  const jobs = useQuery({ queryKey: ["scrape-jobs"], queryFn: api.scrapeJobs });
  // Shares the header pill's cache: warn before a run that Tor can't serve.
  const health = useQuery({ queryKey: ["healthz", "full"], queryFn: api.healthzFull });

  // Poll the active job every 2s while it is queued/running.
  const active = useQuery({
    queryKey: ["scrape-job", activeJobId],
    queryFn: () => api.scrapeJob(activeJobId!),
    enabled: activeJobId !== null,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "done" || s === "error" ? false : 2000;
    },
  });

  const start = useMutation({
    mutationFn: (body: { onion_url: string; skip_llm: boolean }) =>
      api.startScrapeJob(body),
    onSuccess: (job) => {
      setActiveJobId(job.id);
      setUrl("");
      qc.invalidateQueries({ queryKey: ["scrape-jobs"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  function submit() {
    setFormError(null);
    const v = url.trim();
    if (!v) {
      setFormError("Paste a .onion address first.");
      return;
    }
    if (!v.replace(/^https?:\/\//, "").split("/")[0].endsWith(".onion")) {
      setFormError("That isn't a .onion address. It should end in .onion, e.g. abcd…xyz.onion");
      return;
    }
    start.mutate({ onion_url: v, skip_llm: skipLlm });
  }

  // When a polled job finishes, refresh the job list once.
  if (
    active.data &&
    (active.data.status === "done" || active.data.status === "error") &&
    jobs.data &&
    !jobs.data.items.some(
      (j) => j.id === active.data!.id && j.status === active.data!.status,
    )
  ) {
    qc.invalidateQueries({ queryKey: ["scrape-jobs"] });
  }


  const torDown = health.data && health.data.checks.tor_socks?.status !== "up";
  const llmDown = health.data && health.data.checks.ollama?.status !== "up";

  return (
    <div className="mx-auto max-w-[1100px] px-4 pb-24 sm:px-8">
      <SectionDivider label="Scout" trailing="Run the full pipeline on any .onion forum" />

      <form
        className="border border-text bg-surface-1 p-5"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="scout-url" className="mb-2 block text-sm font-bold">
          Forum address
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="scout-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="abcd…xyz.onion"
            aria-invalid={!!formError}
            aria-describedby="scout-help"
            className="min-w-0 flex-1 border border-border-soft bg-surface-1 px-3 py-2 font-mono text-sm focus:border-text focus:outline-none"
          />
          <button
            type="submit"
            disabled={start.isPending}
            className="whitespace-nowrap border border-accent bg-accent px-4 py-2 text-sm font-semibold text-surface-1 hover:bg-accent-strong disabled:opacity-40"
          >
            {start.isPending ? "Starting…" : "Run pipeline"}
          </button>
        </div>
        {formError && (
          <p role="alert" className="m-0 mt-2 text-sm font-semibold text-danger">
            {formError}
          </p>
        )}
        {torDown && (
          <p className="m-0 mt-3 border-l-2 border-warn pl-3 text-sm text-warn">
            The Tor proxy is offline, so a run will stop at the scraping step. Start the Tor
            containers (<span className="font-mono">docker compose up -d</span>) first.
          </p>
        )}

        <label className="mt-4 flex w-fit cursor-pointer select-none items-start gap-2">
          <input
            type="checkbox"
            checked={skipLlm}
            onChange={(e) => setSkipLlm(e.target.checked)}
            className="mt-1 accent-[var(--color-text)]"
          />
          <span className="text-sm">
            <span className="font-semibold">Fast mode</span>
            <span className="text-text-muted">
              {" "}
              skips LLM enrichment: scrape, IOCs and semantic ATT&amp;CK mapping only.
              {llmDown && !skipLlm && " Ollama is offline, so runs will skip the LLM anyway."}
            </span>
          </span>
        </label>

        <p id="scout-help" className="m-0 mt-3 max-w-[70ch] text-sm text-text-muted">
          SentinelX crawls the forum's HTML over Tor, extracts IOCs,{" "}
          {skipLlm ? "skips the LLM," : "enriches each post with the local LLM,"} and maps every post to
          MITRE ATT&amp;CK. Works on any forum with SilkVault's page structure.
        </p>
      </form>

      {active.data && (
        <div className="mt-6">
          <JobProgress job={active.data} />
        </div>
      )}

      <section className="mt-10">
        <h3 className="m-0 mb-3 border-t-2 border-rule pt-2 text-sm font-bold">Recent runs</h3>
        {jobs.isError && <QueryError what="recent runs" error={jobs.error} onRetry={() => jobs.refetch()} />}
        {jobs.isLoading && <p className="text-sm text-text-muted">Loading…</p>}
        {jobs.data && jobs.data.items.length === 0 && (
          <p className="text-sm text-text-muted">No runs yet. Paste a forum address above to start one.</p>
        )}
        <ul className="m-0 list-none p-0">
          {jobs.data?.items.map((j) => (
            <li key={j.id}>
              <button
                onClick={() => setActiveJobId(j.id)}
                aria-current={activeJobId === j.id}
                className={`flex w-full items-baseline gap-3 border-t border-border-soft py-2.5 pl-2 text-left text-sm hover:bg-surface-1 ${
                  activeJobId === j.id ? "bg-surface-1 shadow-[inset_3px_0_0_var(--color-accent)]" : ""
                }`}
              >
                <span className="w-10 font-mono text-text-muted">#{j.id}</span>
                <StatusPill status={j.status} />
                <span className="min-w-0 flex-1 truncate font-mono">{j.source}</span>
                <span className="whitespace-nowrap pr-2 text-xs text-text-muted tabular-nums">
                  {j.posts_scraped} scraped · {j.techniques_mapped} mapped
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function JobProgress({ job }: { job: ScrapeJob }) {
  const current = stageIndex(job.stage);
  const failed = job.status === "error";

  return (
    <div className="border border-text bg-surface-1 p-5" aria-live="polite">
      <div className="mb-4 flex flex-wrap items-baseline gap-3">
        <span className="text-sm font-bold">Run #{job.id}</span>
        <StatusPill status={job.status} />
        <span className="min-w-0 truncate font-mono text-sm text-text-muted">{job.onion_url}</span>
      </div>

      <ol className="m-0 list-none space-y-2 p-0">
        {STAGES.map((s, i) => {
          const state =
            failed && i === current
              ? "failed"
              : i < current || job.status === "done"
                ? "done"
                : i === current
                  ? "active"
                  : "pending";
          return (
            <li key={s.key} className="flex items-center gap-3 text-sm">
              <StageMark state={state} />
              <span
                className={
                  state === "pending"
                    ? "text-text-muted"
                    : state === "failed"
                      ? "font-semibold text-danger"
                      : state === "active"
                        ? "font-semibold"
                        : ""
                }
              >
                {s.label}
              </span>
              {s.key === "llm" && state === "active" && (
                <span className="text-xs text-text-muted">
                  {job.posts_llm > 0 ? `${job.posts_llm} posts` : "starting…"}
                </span>
              )}
              {s.key === "llm" && job.llm_skipped === 1 && (
                <span className="text-xs text-warn">skipped (Ollama offline or fast mode)</span>
              )}
            </li>
          );
        })}
      </ol>

      {job.message && (
        <p className="m-0 mt-4 border-t border-border-soft pt-3 text-sm text-text-muted">{job.message}</p>
      )}
      {job.error && (
        <p role="alert" className="m-0 mt-2 text-sm text-danger">
          <span className="font-semibold">Run failed:</span> <span className="font-mono">{job.error}</span>
        </p>
      )}

      {job.status === "done" && job.posts_scraped > 0 && (
        <dl className="m-0 mt-4 grid grid-cols-3 border-t border-border-soft pt-3 text-sm">
          <Stat n={job.posts_scraped} label="posts scraped" />
          <Stat n={job.posts_llm} label="LLM-analysed" />
          <Stat n={job.techniques_mapped} label="ATT&CK mappings" />
        </dl>
      )}
    </div>
  );
}

/** Stage marker drawn as a shape: done = filled square, active = ring that
 *  pulses (still under reduced motion), failed = red square, pending = hairline. */
function StageMark({ state }: { state: string }) {
  const cls =
    state === "done"
      ? "bg-text"
      : state === "failed"
        ? "bg-danger"
        : state === "active"
          ? "rounded-full border-2 border-accent animate-pulse"
          : "border border-border-soft";
  return <span aria-hidden className={`inline-block h-3 w-3 flex-none ${cls}`} />;
}

function StatusPill({ status }: { status: ScrapeJob["status"] }) {
  const color =
    status === "done" ? "text-ok" : status === "error" ? "text-danger" : "text-warn";
  const label = { done: "Done", error: "Failed", running: "Running", queued: "Queued" }[status];
  return <span className={`text-xs font-semibold ${color}`}>{label}</span>;
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="m-0 text-lg font-bold tabular-nums">{n}</dd>
    </div>
  );
}
